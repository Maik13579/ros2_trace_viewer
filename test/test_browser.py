"""Headless browser exercises the packaged interface against a local test server."""
from pathlib import Path
import os
import socket
import subprocess
import threading
import time

import uvicorn

from ros2_trace_viewer.server import create_app


def test_browser_interface(repository, tmp_path):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(repository.path),
                            log_level='error', lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    script = Path(__file__).with_name('browser_smoke.py')
    env = dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path),
               QTWEBENGINE_DISABLE_SANDBOX='1',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --disable-dev-shm-usage')
    try:
        result = subprocess.run(['xvfb-run', '-a', 'python3', str(script),
            f'http://127.0.0.1:{port}', str(tmp_path / 'screenshots')],
            capture_output=True, text=True, timeout=90, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / 'screenshots' / 'timeline.png').is_file()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
