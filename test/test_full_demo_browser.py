"""Genuine recorded action/call UI coverage, always on an isolated random port."""
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import pytest
import uvicorn
from ros2_trace_viewer.trace.loader import load_trace
from ros2_trace_viewer.server import create_app


@pytest.mark.integration
def test_full_demo_browser(tmp_path):
    trace=os.environ.get('ROS2_TRACE_VIEWER_TEST_FULL_TRACE')
    if not trace:pytest.skip('Set ROS2_TRACE_VIEWER_TEST_FULL_TRACE for the recorded full demo')
    with load_trace(trace,progress=lambda _:None,work_dir=tmp_path) as database:
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        assert port not in (8080,8081)
        server=uvicorn.Server(uvicorn.Config(create_app(database),log_level='error',lifespan='off'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            deadline=time.monotonic()+10
            while not server.started and time.monotonic()<deadline:time.sleep(.01)
            assert server.started
            env=dict(os.environ,XDG_RUNTIME_DIR=str(tmp_path),QTWEBENGINE_DISABLE_SANDBOX='1',
                     QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --disable-dev-shm-usage')
            result=subprocess.run(['xvfb-run','-a','python3',str(Path(__file__).with_name('browser_full_demo.py')),
                f'http://127.0.0.1:{port}',str(tmp_path/'screenshots')],
                capture_output=True,text=True,timeout=90,env=env)
            assert result.returncode==0,result.stdout+result.stderr
        finally:
            server.should_exit=True;thread.join(timeout=10);sock.close()
