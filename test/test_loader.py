from pathlib import Path

import pytest

from conftest import event, execution, node, timer
from ros2_trace_viewer.cli import main, parser
from ros2_trace_viewer.trace.loader import TraceError, load_trace, validate_trace_path


def test_trace_path_validation(tmp_path):
    with pytest.raises(TraceError, match='does not exist'):
        validate_trace_path(tmp_path / 'missing')
    file = tmp_path / 'plain-file'; file.write_text('not a trace')
    with pytest.raises(TraceError, match='not a file'):
        validate_trace_path(file)
    with pytest.raises(TraceError, match='No LTTng CTF'):
        validate_trace_path(tmp_path)
    metadata = tmp_path / 'ust' / 'uid' / '0' / '64-bit' / 'metadata'
    metadata.parent.mkdir(parents=True); metadata.write_text('/* CTF 1.8 */')
    root, directories = validate_trace_path(tmp_path)
    assert root == tmp_path and directories == [metadata.parent]
    metadata.write_bytes(b'')
    with pytest.raises(TraceError, match='Empty CTF'):
        validate_trace_path(tmp_path)


def test_read_only_source_and_index_cleanup(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'metadata').write_text('CTF test loader fixture')
    monkeypatch.setattr('ros2_trace_viewer.trace.loader.read_events',
        lambda _: iter([node(), *timer(), *execution(1, 2)]))
    before = {(p.name, p.read_bytes()) for p in source.iterdir()}
    with load_trace(source, progress=lambda _: None, work_dir=tmp_path) as database:
        assert database.is_file() and source not in database.parents
        index = database
    assert not index.exists()
    assert before == {(p.name, p.read_bytes()) for p in source.iterdir()}
    with pytest.raises(TraceError, match='outside'):
        with load_trace(source, work_dir=source):
            pass


def test_no_complete_or_initialized_callbacks_error(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir(); (source / 'metadata').write_text('CTF')
    monkeypatch.setattr('ros2_trace_viewer.trace.loader.read_events', lambda _: iter([node()]))
    with pytest.raises(TraceError, match='No complete ROS 2'):
        with load_trace(source, progress=lambda _: None, work_dir=tmp_path):
            pass
    monkeypatch.setattr('ros2_trace_viewer.trace.loader.read_events', lambda _: iter([node(), *execution(1, 2)]))
    with pytest.raises(TraceError, match='No initialized callbacks'):
        with load_trace(source, progress=lambda _: None, work_dir=tmp_path):
            pass


def test_cli_defaults_and_errors(tmp_path, capsys):
    args = parser().parse_args(['--trace-dir', str(tmp_path)])
    assert args.host == '0.0.0.0' and args.port == 8080
    assert main(['--trace-dir', str(tmp_path / 'missing')]) == 1
    assert 'does not exist' in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(['--port', '0', str(tmp_path)])
    with pytest.raises(SystemExit):
        main([])
