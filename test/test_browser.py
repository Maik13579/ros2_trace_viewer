"""Headless browser exercises the packaged interface against a local test server."""
from pathlib import Path
import os
import socket
import subprocess
import threading
import time

import uvicorn

from ros2_trace_viewer.server import create_app
from ros2_trace_viewer.trace.repository import Repository
from conftest import repository_events, event, execution
from test_communication import service


def test_browser_interface(make_repository, tmp_path, monkeypatch):
    events = repository_events() + [
        event('rcl_publisher_init',0,publisher_handle=910,node_handle=100,
              rmw_publisher_handle=911,topic_name='/secondary',queue_depth=10),
        *service(), *service(810,410,'/robot/ns/node/get_parameters'),
        *service(820,420,'/robot/get_parameters_extra'),
        *service(830,430,'/other/node/get_type_description'),
        *execution(6,9,callback=800),*execution(60,63,callback=810),
        *execution(90,94,callback=820)]
    repository = make_repository(events)[0]
    dimensions = Repository.dimensions
    def without_default_metadata(self):
        # The browser must match suffixes itself, even without a precomputed list.
        return {key:value for key,value in dimensions(self).items() if key!='default_services'}
    monkeypatch.setattr(Repository,'dimensions',without_default_metadata)
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
