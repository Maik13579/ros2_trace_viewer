#!/usr/bin/env python3
"""Record actual installed ROS examples in an isolated ROS domain.

Only processes started here are traced and stopped. Existing LTTng sessions and
recordings are untouched. The viewer itself remains entirely offline.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=25)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be a new trace directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = Path('/opt/ros') / os.environ.get('ROS_DISTRO', 'jazzy') / 'lib'
    commands = [
        [str(prefix / 'composition/manual_composition')],
        [str(prefix / 'demo_nodes_cpp/talker'), '--ros-args', '-r', '__node:=example_talker',
         '-r', 'chatter:=/viewer_examples/chatter'],
        [str(prefix / 'demo_nodes_cpp/listener'), '--ros-args', '-r', '__node:=example_listener',
         '-r', 'chatter:=/viewer_examples/chatter'],
        [str(prefix / 'demo_nodes_cpp/reuse_timer')],
    ]
    env = dict(os.environ, ROS_DOMAIN_ID='187', ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST',
               ROS_LOG_DIR=str(output.parent / 'ros_logs'))
    session = 'viewer_examples_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    processes, logs = [], []
    created = started = False

    def lttng(*arguments):
        subprocess.run(['lttng', *arguments], check=True, timeout=20)

    try:
        for index, command in enumerate(commands):
            log = (output.parent / f'{session}_node_{index}.log').open('w')
            logs.append(log)
            # Stop before exec so tracing can be enabled for the exact PID
            # before the ROS initialization events run.
            process = subprocess.Popen(['/bin/bash', '-c', 'kill -STOP $$; exec "$@"',
                                         'trace-example', *command], env=env,
                                        stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            os.waitpid(process.pid, os.WUNTRACED)
        lttng('create', session, '--output', str(output)); created = True
        lttng('enable-channel', '--userspace', 'callbacks', '--subbuf-size=1M', '--num-subbuf=4')
        lttng('add-context', '--userspace', '--channel=callbacks',
              '-t', 'vpid', '-t', 'vtid', '-t', 'procname')
        expression = ' || '.join(f'$ctx.vpid == {p.pid}' for p in processes)
        lttng('enable-event', '--userspace', '--channel=callbacks', 'ros2:*', '--filter', expression)
        lttng('start', session); started = True
        for process in processes:
            process.send_signal(signal.SIGCONT)
        print(f'Recording real ROS 2 examples for {args.seconds:g} seconds in domain 187…', flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError('An example exited early; inspect the node logs beside the trace')
            time.sleep(.25)
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGCONT)
                process.send_signal(signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        if started:
            lttng('stop', session)
        if created:
            lttng('destroy', session)
        for log in logs:
            log.close()
    print(f'Finished recording: {output}', flush=True)


if __name__ == '__main__':
    main()
