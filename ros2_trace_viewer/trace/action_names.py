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
