"""Offline CLI entry points. Loading finishes before the HTTP socket opens."""
import argparse
from pathlib import Path
import socket
import sys

from .trace.loader import TraceError, load_trace


def parser():
    result = argparse.ArgumentParser(description='View a finished ROS 2 LTTng CTF trace in a browser.')
    result.add_argument('trace', nargs='?', help='Recorded trace directory (alternative to --trace-dir)')
    result.add_argument('--trace-dir', type=Path, help='Recorded trace directory, read-only')
    result.add_argument('--host', default='0.0.0.0', help='HTTP bind address (default: 0.0.0.0)')
    result.add_argument('--port', type=int, default=8080, help='HTTP port (default: 8080)')
    result.add_argument('--work-dir', type=Path, help='Scratch directory outside the trace for the index')
    return result


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if bool(args.trace) == bool(args.trace_dir):
        arguments.error('provide exactly one trace path: --trace-dir PATH or positional PATH')
    if not 1 <= args.port <= 65535:
        arguments.error('--port must be between 1 and 65535')
    try:
        import uvicorn
        from .server import create_app
        with load_trace(args.trace_dir or args.trace,
                        progress=lambda message: print(message, flush=True),
                        work_dir=args.work_dir) as database:
            local_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
            if ':' in local_host:
                local_host = f'[{local_host}]'
            print(f'Open http://{local_host}:{args.port}', flush=True)
            if args.host == '0.0.0.0':
                print(f'Network: http://{socket.gethostname()}:{args.port} '
                      '(or use this machine\'s IP address)', flush=True)
            uvicorn.run(create_app(database), host=args.host, port=args.port,
                        access_log=False, log_level='info')
    except KeyboardInterrupt:
        return 0
    except (TraceError, ValueError, OSError, RuntimeError) as error:
        print(f'ros2_trace_viewer: {error}', file=sys.stderr)
        return 1
    return 0
