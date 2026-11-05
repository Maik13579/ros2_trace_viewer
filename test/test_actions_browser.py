"""Browser coverage for inferred actions, including a genuine recording locally."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

from ros2_trace_viewer.server import create_app
from ros2_trace_viewer.trace.loader import load_trace
from test_actions import action_events


@pytest.mark.parametrize('source', ['synthetic', 'real'])
def test_action_browser(source, make_repository, tmp_path):
    @contextmanager
    def database():
        if source == 'synthetic':
            yield make_repository(action_events())[0].path
        else:
            trace = os.environ.get('ROS2_TRACE_VIEWER_TEST_ACTION_TRACE')
            if not trace:
                pytest.skip('Set ROS2_TRACE_VIEWER_TEST_ACTION_TRACE for genuine action browser test')
            with load_trace(trace, progress=lambda _: None, work_dir=tmp_path) as path:
                yield path

    with database() as path:
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(path), log_level='error', lifespan='off'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start(); deadline = time.monotonic()+10
        while not server.started and time.monotonic()<deadline: time.sleep(.01)
        assert server.started
        env = dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path), QTWEBENGINE_DISABLE_SANDBOX='1',
                   QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --disable-dev-shm-usage')
        try:
            result = subprocess.run(['xvfb-run','-a','python3',str(Path(__file__).with_name('browser_actions.py')),
                f'http://127.0.0.1:{port}',str(tmp_path/'screenshots')],
                capture_output=True, text=True, timeout=90, env=env)
            assert result.returncode == 0, result.stdout+result.stderr
            assert (tmp_path/'screenshots'/'action-details.png').is_file()
        finally:
            server.should_exit=True;thread.join(timeout=10);sock.close()
