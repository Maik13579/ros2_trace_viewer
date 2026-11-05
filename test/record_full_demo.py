#!/usr/bin/env python3
"""Build and record real ROS demos with explicit action and call instrumentation.

Creates no HTTP server. All logs/builds are beside the new, immutable CTF trace.
Only this helper's two ROS processes and uniquely named LTTng session are stopped.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--domain',type=int,default=187)
    parser.add_argument('--skip-build',action='store_true')
    parser.add_argument('--kernel',action='store_true',help='Try optional sched_switch recording')
    args=parser.parse_args();output=args.output.resolve()
    if output.exists():parser.error('Output must be a new trace directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    build=output.parent/'build'
    if not args.skip_build:
        subprocess.run(['cmake','-S',str(Path(__file__).with_name('action_demo')),
                        '-B',str(build),'-DCMAKE_BUILD_TYPE=Release'],check=True)
        subprocess.run(['cmake','--build',str(build),'--parallel','4'],check=True)
    program=build/'action_demo'
    if not program.is_file():parser.error('Demo executable not built')
    log_home=output.parent/'ros_logs';log_home.mkdir(exist_ok=True)
    lttng_home=output.parent/'lttng_home';lttng_home.mkdir(exist_ok=True)
    env=dict(os.environ,ROS_DOMAIN_ID=str(args.domain),ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST',
             ROS_LOCALHOST_ONLY='1',ROS_LOG_DIR=str(log_home),LTTNG_HOME=str(lttng_home))
    session='viewer_full_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    processes=[];logs=[];created=started=kernel=False
    def lttng(*values):subprocess.run(['lttng',*values],env=env,check=True,timeout=20)
    try:
        for role in ('server','client'):
            log=(output.parent/(role+'.log')).open('w');logs.append(log)
            process=subprocess.Popen(['/bin/bash','-c','kill -STOP $$; exec "$@"','viewer-demo',
                str(program),role,'--ros-args','-r','__ns:=/viewer_demo'],
                env=env,stdout=log,stderr=subprocess.STDOUT)
            processes.append(process);os.waitpid(process.pid,os.WUNTRACED)
        lttng('create',session,'--output',str(output));created=True
        lttng('enable-channel','--session',session,'--userspace','demo','--subbuf-size=1M','--num-subbuf=4')
        lttng('add-context','--session',session,'--userspace','--channel=demo',
              '-t','vpid','-t','vtid','-t','procname')
        pids=' || '.join(f'$ctx.vpid == {process.pid}' for process in processes)
        lttng('enable-event','--session',session,'--userspace','--channel=demo','ros2:*',
              '--exclude','ros2:rclcpp_executor_*','--filter',pids)
        lttng('enable-event','--session',session,'--userspace','--channel=demo',
              'ros2_trace_viewer:*','--filter',pids)
        if args.kernel:
            available=subprocess.run(['lttng','list','--kernel'],env=env,capture_output=True,timeout=20)
            if available.returncode==0:
                lttng('enable-event','--session',session,'--kernel','sched_switch');kernel=True
            else:print('Kernel scheduler tracing unavailable; continuing with user-space events.',flush=True)
        lttng('start',session);started=True
        processes[0].send_signal(signal.SIGCONT);time.sleep(.5)
        processes[1].send_signal(signal.SIGCONT)
        result=processes[1].wait(timeout=35)
        if result or 'ALL_SCENARIOS_PASSED' not in (output.parent/'client.log').read_text():
            raise RuntimeError('Demo failed; inspect client.log and server.log')
        if processes[0].poll() is not None:raise RuntimeError('Server exited unexpectedly')
        time.sleep(.5)
    finally:
        for process in processes:
            if process.poll() is None:process.send_signal(signal.SIGCONT);process.send_signal(signal.SIGINT)
        for process in processes:
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if started:lttng('stop',session)
        if created:lttng('destroy',session)
        for log in logs:log.close()
    (output.parent/'demo.json').write_text(json.dumps({
        'trace':str(output),'ros_distro':os.environ.get('ROS_DISTRO'),'domain':args.domain,
        'instrumentation':'ros2_trace_viewer:action_callback_init + ros2:callback_start/end + client_call/end',
        'scenarios':['timer publication','subscription with publication','asynchronous service calls',
            'blocking service calls','successful actions','cancelled action','rejected action'],
        'kernel_scheduler':kernel,'processes':dict(zip(('server','client'),[p.pid for p in processes]))},indent=2)+'\n')
    print('Recorded trace: '+str(output),flush=True)


if __name__=='__main__':main()
