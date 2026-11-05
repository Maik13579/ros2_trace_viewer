"""Exact statistics over fully contained executions, with paged hierarchy levels."""
import numpy as np

from .filters import JOIN

GROUPS = {
    'process': ('CAST(e.pid AS TEXT)', "coalesce(p.name,'Unknown process')||' ['||e.pid||']'"),
    'node': ("coalesce(c.node_id,'unknown:'||e.pid)", "coalesce(n.name,'Unknown node')"),
    'callback': ('c.id', "coalesce(c.symbol,c.pointer)"),
}
SORTS = {'name', 'count', 'total_ns', 'min_ns', 'max_ns', 'mean_ns',
         'median_ns', 'stddev_ns', 'p95_ns', 'p99_ns', 'share_percent', 'interval_percent'}


def summarize(values, include_boxplot=False):
    """Population standard deviation, linear interpolated quantiles, exact totals."""
    if not values.size:
        return {'count': 0}
    quantiles = np.quantile(values, [.5, .95, .99] + ([.25, .75] if include_boxplot else []))
    result = {
        'count': int(values.size),
        'total_ns': str(sum(map(int, values))),
        'min_ns': str(int(values.min())), 'max_ns': str(int(values.max())),
        'mean_ns': float(values.mean()), 'median_ns': float(quantiles[0]),
        'stddev_ns': float(values.std()), 'p95_ns': float(quantiles[1]),
        'p99_ns': float(quantiles[2]),
    }
    if include_boxplot:
        result.update(boxplot_summary(values, quantiles[3], quantiles[4]))
    return result


def boxplot_summary(values, q1, q3, outlier_limit=64):
    """Tukey whiskers use observed values within 1.5 IQR of the quartiles.

    Outlier counts are exact. At most 64 observed outlier durations per callback
    are returned, sampled deterministically across their sorted ranks. Repeated
    values remain observations, not invented points or percentile estimates.
    """
    iqr = float(q3 - q1)
    outlier_mask = (values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr)
    inliers = ~outlier_mask
    outliers = np.sort(values[outlier_mask])
    count = int(outliers.size)
    if count > outlier_limit:
        indices = np.linspace(0, count - 1, outlier_limit, dtype=np.int64)
        outliers = outliers[indices]
    return {
        'q1_ns': float(q1), 'q3_ns': float(q3),
        'lower_whisker_ns': str(int(np.min(values, where=inliers, initial=np.iinfo(np.int64).max))),
        'upper_whisker_ns': str(int(np.max(values, where=inliers, initial=0))),
        'outlier_count': count, 'outliers_ns': [str(int(value)) for value in outliers],
        'outliers_sampled': count > outlier_limit,
    }


def statistics(db, filters, level='process', sort='total_ns', descending=True,
               offset=0, limit=100, include_boxplot=False):
    """Aggregate one hierarchy level. Range crossings are reported separately.

    SQLite sorts and scans on disk. Exact quantiles retain only one group's
    int64 duration vector at a time (8 bytes per execution), not event objects.
    A bounded LRU in the repository caches completed responses.
    """
    if level not in GROUPS or sort not in SORTS:
        raise ValueError('Unsupported statistics level or sort column')
    group, label = GROUPS[level]
    where, params = filters.sql('contained')
    groups = db.execute(f'''SELECT {group} id,{label} name,e.pid,c.node_id,c.type,n.name node_name,
        c.topic callback_topic,c.service callback_service,c.timer_period_ns,
        c.action callback_action,c.action_role,c.action_side,
        count(*) count,sum_ns(e.duration_ns) total_ns,
        min(e.duration_ns) min_ns,max(e.duration_ns) max_ns,avg(e.duration_ns) mean_ns
        FROM {JOIN} WHERE {where} GROUP BY {group}''', params).fetchall()
    total_callback_ns = sum(int(r['total_ns']) for r in groups)
    interval_ns = max(0, filters.end - filters.start)
    if sort in {'share_percent', 'interval_percent'}:
        sort = 'total_ns'
    # Sort cheap aggregate columns before loading the duration vectors. Exact
    # quantile/stddev sorting must evaluate every group before applying a page.
    expensive = sort in {'median_ns', 'stddev_ns', 'p95_ns', 'p99_ns'}
    if not expensive:
        if sort == 'name' and level == 'callback':
            # Sort by the node/resource label shown in callback lists; keep the
            # original symbol in `name` for the complete metadata inspector.
            key = lambda r: ((r['node_name'] or 'Unknown node').casefold(),
                             (r['callback_action'] or r['callback_topic'] or r['callback_service'] or '').casefold(),
                             r['type'],r['id'])
        else:
            key = lambda r: int(r[sort]) if sort == 'total_ns' else r[sort]
        groups = sorted(groups,key=key,reverse=descending)
        selected = groups[offset:offset + limit]
    else:
        selected = groups
    rows = []
    for row in selected:
        durations = np.fromiter((r[0] for r in db.execute(
            f'SELECT e.duration_ns FROM {JOIN} WHERE {where} AND {group}=?',
            [*params, row['id']])), dtype=np.int64, count=row['count'])
        rows.append({'id': row['id'], 'name': row['name'], 'pid': row['pid'],
                     'node_id': row['node_id'], 'type': row['type'], 'node_name': row['node_name'],
                     'callback_topic': row['callback_topic'], 'callback_service': row['callback_service'],
                     'callback_action':row['callback_action'], 'action_role':row['action_role'],
                     'action_side':row['action_side'],
                     'timer_period_ns': str(row['timer_period_ns']) if level=='callback' and row['timer_period_ns'] is not None else None,
                     **summarize(durations, include_boxplot=include_boxplot)})
    for row in rows:
        row['share_percent'] = (int(row['total_ns']) / total_callback_ns * 100) if total_callback_ns else 0
        row['interval_percent'] = (int(row['total_ns']) / interval_ns * 100) if interval_ns else None
    if expensive:
        rows.sort(key=lambda r: int(r[sort]) if sort == 'total_ns' else r[sort], reverse=descending)
        rows = rows[offset:offset + limit]
    overlap_where, overlap_params = filters.sql()
    crossing = db.execute(f'''SELECT count(*) FROM {JOIN} WHERE {overlap_where}
        AND (e.start_ns<? OR e.end_ns>?)''',
        [*overlap_params, filters.start, filters.end]).fetchone()[0]
    result = {'level': level, 'rows': rows, 'total_groups': len(groups),
              'execution_count': sum(r['count'] for r in groups), 'crossing_count': crossing,
              'offset': offset, 'limit': limit,
              'total_callback_ns': str(int(total_callback_ns)), 'interval_ns': str(interval_ns)}
    if include_boxplot:
        # One scale across every page, rather than silently rescaling per page.
        result['axis_max_ns'] = str(max((r['max_ns'] for r in groups), default=0))
    return result


def charts(db, filters, bins=64):
    """Bounded, exact duration histogram and time buckets, plus top callbacks."""
    where, params = filters.sql('contained')
    bounds = db.execute(f'SELECT min(e.duration_ns), max(e.duration_ns),count(*) '
                        f'FROM {JOIN} WHERE {where}', params).fetchone()
    if not bounds[2]:
        return {'histogram': [], 'over_time': [], 'top': [], 'count': 0}
    low, high, count = bounds
    width = max(1, (high - low + 1) / bins)
    histogram = [dict(r) for r in db.execute(f'''SELECT
        CAST((e.duration_ns-?)/? AS INTEGER) bin,count(*) count
        FROM {JOIN} WHERE {where} GROUP BY bin ORDER BY bin''', [low, width, *params])]
    for row in histogram:
        row['start_ns'] = low + row['bin'] * width
        row['end_ns'] = low + (row['bin'] + 1) * width
    end = min(filters.end, db.execute('SELECT max(end_ns) FROM executions').fetchone()[0])
    step = max(1, (end - filters.start + 1) / bins)
    over_time = [dict(r) for r in db.execute(f'''SELECT
        CAST((e.start_ns-?)/? AS INTEGER) bin,count(*) count,
        avg(e.duration_ns) mean_ns,max(e.duration_ns) max_ns
        FROM {JOIN} WHERE {where} GROUP BY bin ORDER BY bin''',
        [filters.start, step, *params])]
    for row in over_time:
        row['start_ns'] = str(int(filters.start + row['bin'] * step))
    top = [dict(r) for r in db.execute(f'''SELECT c.id,coalesce(c.symbol,c.pointer) name,c.type,c.topic callback_topic,c.service callback_service,c.action callback_action,c.action_role,c.action_side,n.name node_name,
        sum_ns(e.duration_ns) total_ns,count(*) count FROM {JOIN} WHERE {where}
        GROUP BY c.id ORDER BY length(total_ns) DESC,total_ns DESC LIMIT 12''', params)]
    return {'histogram': histogram, 'over_time': over_time, 'top': top, 'count': count}
