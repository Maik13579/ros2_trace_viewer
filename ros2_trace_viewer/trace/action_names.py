"""Infer action transport groups from the standard ROS 2 endpoint convention.

This is naming evidence, not an action/goal tracepoint or proof of delivery.
Keep the complete namespace so identically named actions remain independent.
"""
ROLES = {
    'service': ('send_goal', 'cancel_goal', 'get_result'),
    'topic': ('feedback', 'status'),
}


def action_channel(name, transport):
    """Return (fully qualified action name, role), or None for other endpoints."""
    if not isinstance(name, str) or not name.startswith('/'):
        return None
    base, separator, role = name.rpartition('/_action/')
    if separator and base and role in ROLES.get(transport, ()):
        return base, role
    return None


def recorded_action_roles(db, end=None):
    """List recorded channel roles, optionally before a trace-relative timestamp."""
    condition = ' WHERE created_ns<=?' if end is not None else ''
    params = [end, end] if end is not None else []
    rows = db.execute("SELECT 'topic',topic FROM endpoints" + condition +
                      " UNION SELECT 'service',name FROM service_endpoints" + condition, params)
    actions = {}
    for transport, name in rows:
        parsed = action_channel(name, transport)
        if parsed:
            base, role = parsed
            actions.setdefault(base, set()).add(role)
    return {base: sorted(roles) for base, roles in actions.items()}


def index_actions(db):
    """Index transport membership and each related callback once per action."""
    rows = db.execute("SELECT 'topic',topic FROM endpoints UNION "
                      "SELECT 'service',name FROM service_endpoints").fetchall()
    for transport, name in rows:
        parsed = action_channel(name, transport)
        if parsed:
            db.execute('INSERT OR IGNORE INTO action_channels VALUES (?,?,?,?)',
                       (transport, name, *parsed))
    db.execute('''INSERT OR IGNORE INTO execution_actions
        SELECT et.execution_id,ac.action FROM execution_topics et JOIN action_channels ac
        ON ac.transport='topic' AND ac.name=et.topic''')
    db.execute('''INSERT OR IGNORE INTO execution_actions SELECT e.id,ac.action
        FROM executions e JOIN callbacks c ON c.id=e.callback_id JOIN action_channels ac
        ON ac.transport='service' AND ac.name=c.service WHERE c.type='service' ''')


def action_member(column, transport):
    """SQL membership predicate; column and transport are internal identifiers."""
    return (f"EXISTS (SELECT 1 FROM action_channels arc WHERE arc.transport='{transport}' "
            f'AND arc.name={column})')


def resource_scope(filters, column, transport):
    """Keep topic/service choices independent of the logical Actions category."""
    clauses, params = [], []
    member = action_member(column, transport)
    selected = filters.topics if transport == 'topic' else filters.services
    if selected:
        clauses.append(f'({member} OR {column} IN ({",".join("?" for _ in selected)}))')
        params.extend(selected)
    if filters.actions:
        clauses.append(f"(NOT {member} OR EXISTS (SELECT 1 FROM action_channels ars "
            f"WHERE ars.transport='{transport}' AND ars.name={column} "
            f'AND ars.action IN ({",".join("?" for _ in filters.actions)})))')
        params.extend(filters.actions)
    return ' AND '.join(clauses) or '1', params


def accepts_resource(filters, name, transport):
    parsed = action_channel(name, transport)
    if parsed:
        return not filters.actions or parsed[0] in filters.actions
    selected = filters.topics if transport == 'topic' else filters.services
    return not selected or name in selected
