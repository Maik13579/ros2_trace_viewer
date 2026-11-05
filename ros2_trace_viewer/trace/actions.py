"""Action transport summaries, reusing the topic/service filter and query policy."""
from .action_names import action_channel, recorded_action_roles
from .topics import topic_data
from .services import service_data
from .filters import JOIN


def action_data(db, filters, trace_end):
    """Aggregate metadata and counters, never goal states or inferred durations.

    Ordinary topic/service choices are independent of the Actions selection.
    Only accepted action channels enter the response. Peers are recorded endpoint
    declarations; they do not establish which client sent a particular goal.
    The repository caches these metadata-sized results before bounded paging.
    """
    recorded = recorded_action_roles(db, min(filters.end, trace_end))
    if not recorded or filters.none:
        return []
    actions = {}
    def empty(base):
        return {'action':base,'action_type':None,'recorded_roles':recorded.get(base,[]),
            'channels':[],'goal_count':None,'goal_ids_available':False,'goal_timing_available':False,
            'callback_count':0,'execution_count':0,'total_callback_ns':'0',
            'callback_timing_available':False}
    # These internal queries aggregate in SQLite, not Python execution arrays.
    # The HTTP response is paginated separately after grouping the five roles.
    for transport, data in (
        ('topic', topic_data(db, filters, trace_end, limit=2**63-1, include_actions=True)),
        ('service', service_data(db, filters, trace_end, limit=2**63-1, include_actions=True)),
    ):
        for channel in data['items']:
            parsed = action_channel(channel[transport], transport)
            if not parsed:
                continue
            base, role = parsed
            item = actions.setdefault(base, empty(base))
            item['channels'].append({'role': role, 'transport': transport, **channel})
    where, params = filters.sql('contained')
    owners = {}
    for row in db.execute(f'''SELECT c.action,c.action_side,n.name node_name,
        count(*) execution_count,count(DISTINCT c.id) callback_count,
        sum_ns(e.duration_ns) total_callback_ns FROM {JOIN}
        WHERE {where} AND c.type='action' GROUP BY c.action,c.action_side,c.node_id''',params):
        item = actions.setdefault(row['action'],empty(row['action']))
        item['execution_count'] += row['execution_count']
        item['callback_count'] += row['callback_count']
        item['total_callback_ns'] = str(int(item['total_callback_ns'])+int(row['total_callback_ns']))
        item['callback_timing_available'] = True
        owners.setdefault((row['action'],row['action_side']),set()).add(row['node_name'] or 'Unknown node')
    role_order = {role: index for index, role in enumerate(
        ('send_goal', 'cancel_goal', 'get_result', 'feedback', 'status'))}
    for item in actions.values():
        item['channels'].sort(key=lambda channel: role_order[channel['role']])
        servers = set(owners.get((item['action'],'server'),()))
        clients = set(owners.get((item['action'],'client'),()))
        first, last = [], []
        for channel in item['channels']:
            servers.update(channel.get('server_nodes', channel.get('publisher_nodes', [])))
            clients.update(channel.get('client_nodes', channel.get('subscriber_nodes', [])))
            if channel.get('first_ns') is not None:
                first.append(int(channel['first_ns']))
                last.append(int(channel['last_ns']))
        item.update(server_nodes=sorted(servers), client_nodes=sorted(clients),
                    first_ns=str(min(first)) if first else None,
                    last_ns=str(max(last)) if last else None)
    return sorted(actions.values(), key=lambda item: item['action'])
