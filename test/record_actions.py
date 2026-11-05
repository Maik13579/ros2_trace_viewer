#!/usr/bin/env python3
"""Record installed C++ action examples: a successful goal, then a cancellation.

The viewer never starts ROS nodes. This separate development helper owns and
cleans up only its three demo processes and its uniquely named tracing session.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from ament_index_python.packages import get_package_prefix


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--domain',type=int,default=188)
    parser.add_argument('--namespace',default='/viewer_actions')
    parser.add_argument('--timeout',type=float,default=35)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists():parser.error('output must be a new trace directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    server_package='examples_rclcpp_minimal_action_server'
    client_package='examples_rclcpp_minimal_action_client'
    server=Path(get_package_prefix(server_package))/'lib'/server_package/'action_server_not_composable'
    clients=Path(get_package_prefix(client_package))/'lib'/client_package
    programs=[server,clients/'action_client_member_functions',clients/'action_client_not_composable_with_cancel']
    names=['action_server','success_client','cancel_client']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    session='viewer_actions_'+stamp
    log_home=output.parent/'ros_logs';lttng_home=output.parent/'lttng_home'
    log_home.mkdir(exist_ok=True);lttng_home.mkdir(exist_ok=True)
    env=dict(os.environ,ROS_DOMAIN_ID=str(args.domain),ROS_AUTOMATIC_DISCOVERY_RANGE='LOCALHOST',
             ROS_LOCALHOST_ONLY='1',ROS_LOG_DIR=str(log_home),LTTNG_HOME=str(lttng_home))
    processes=[];logs=[];created=started=False

    def lttng(*arguments):
        subprocess.run(['lttng',*arguments],env=env,check=True,timeout=20)

    try:
        for program,name in zip(programs,names):
            log=(output.parent/(name+'.log')).open('w');logs.append(log)
            command=[str(program),'--ros-args','-r','__node:='+name,'-r','__ns:='+args.namespace]
            process=subprocess.Popen(['/bin/bash','-c','kill -STOP $$; exec "$@"','action-demo',*command],
                                     env=env,stdout=log,stderr=subprocess.STDOUT)
            processes.append(process);os.waitpid(process.pid,os.WUNTRACED)
        lttng('create',session,'--output',str(output));created=True
        lttng('enable-channel','--session',session,'--userspace','actions','--subbuf-size=1M','--num-subbuf=4')
        lttng('add-context','--session',session,'--userspace','--channel=actions','-t','vpid','-t','vtid','-t','procname')
        pids=' || '.join(f'$ctx.vpid == {p.pid}' for p in processes)
        # The example's spin_some loop emits millions of executor polling events.
        # Keep every ROS initialization, callback, publish and take tracepoint.
        lttng('enable-event','--session',session,'--userspace','--channel=actions','ros2:*',
              '--exclude','ros2:rclcpp_executor_*','--filter',pids)
        lttng('start',session);started=True
        processes[0].send_signal(signal.SIGCONT)
        time.sleep(.5)
        for index in [1,2]:
            print('Running '+names[index]+' in ROS domain '+str(args.domain),flush=True)
            processes[index].send_signal(signal.SIGCONT)
            result=processes[index].wait(timeout=args.timeout)
            cancelled=(index==2 and result==1 and 'Goal was canceled' in
                       (output.parent/'cancel_client.log').read_text())
            if result!=0 and not cancelled:
                raise RuntimeError(f'{names[index]} failed with {result}; see its log')
            if processes[0].poll() is not None:raise RuntimeError('Action server exited unexpectedly')
            time.sleep(.5)
        print('Both action scenarios finished.',flush=True)
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGCONT);process.send_signal(signal.SIGINT)
        for process in processes:
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if started:lttng('stop',session)
        if created:lttng('destroy',session)
        for log in logs:log.close()
    metadata={'trace':str(output),'ros_distro':os.environ.get('ROS_DISTRO'),
              'domain':args.domain,'action':args.namespace.rstrip('/')+'/fibonacci',
              'demo_type':'example_interfaces/action/Fibonacci',
              'scenarios':['successful goal','cancelled goal'],
              'pids':dict(zip(names,[p.pid for p in processes])),
              'excluded_tracepoints':['ros2:rclcpp_executor_*']}
    (output.parent/'demo.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print('Recorded trace: '+str(output),flush=True)


if __name__=='__main__':main()
