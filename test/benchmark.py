#!/usr/bin/env python3
"""Streaming million-execution benchmark, independent of genuine CTF validation."""
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

from ros2_trace_viewer.trace.filters import Filters
from ros2_trace_viewer.trace.models import create_database
from ros2_trace_viewer.trace.processor import Processor
from ros2_trace_viewer.trace.repository import Repository


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    started = time.perf_counter()
    def event(name, timestamp=0, **fields):
        return dict(_name='ros2:' + name, _timestamp=timestamp, vpid=1, vtid=11,
                    cpu_id=0, procname='benchmark', **fields)
    with tempfile.TemporaryDirectory(prefix='trace-benchmark-', dir=work) as directory:
        path = Path(directory) / 'index.sqlite'
        processor = Processor(create_database(path))
        for node in range(8):
            processor.consume(event('rcl_node_init', node_handle=node+1,
                rmw_handle=node+100, node_name=f'node_{node}', namespace='/'))
        for callback in range(64):
            pointer = callback+1000
            processor.consume(event('rcl_timer_init', timer_handle=pointer, period=1000))
            processor.consume(event('rclcpp_timer_link_node', timer_handle=pointer, node_handle=callback%8+1))
            processor.consume(event('rclcpp_timer_callback_added', timer_handle=pointer, callback=pointer))
            processor.consume(event('rclcpp_callback_register', callback=pointer, symbol=f'callback_{callback}()'))
        for index in range(count):
            processor.consume(event('callback_start', index*1000+1, callback=index%64+1000))
            processor.consume(event('callback_end', index*1000+501, callback=index%64+1000))
            if index and index % 200000 == 0:
                print(f'Indexed {index:,} executions', flush=True)
        processor.finalize('benchmark'); processor.db.close()
        loaded = time.perf_counter()
        repository = Repository(path)
        narrow = repository.timeline(Filters(), 'node', 100000, 200000)
        queried = time.perf_counter()
        dense = repository.timeline(Filters(), 'node', 0, count*1000, budget=10000)
        aggregated = time.perf_counter()
        statistics = repository.statistics(Filters(), 'callback', 'total_ns', True, 0, 100)
        completed = time.perf_counter()
        assert len(narrow['records']) == 100
        assert dense['mode'] == 'density' and len(dense['density']) <= 8*256
        assert statistics['execution_count'] == count
        assert int(statistics['total_callback_ns']) == count*500
        print(json.dumps({'executions':count,'index_seconds':loaded-started,
            'narrow_query_seconds':queried-loaded,'density_query_seconds':aggregated-queried,
            'statistics_seconds':completed-aggregated,'database_mb':path.stat().st_size/1024**2,
            'peak_rss_mb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
            'browser_records':len(narrow['records']),'browser_density_cells':len(dense['density'])},indent=2))


if __name__ == '__main__':
    main()
