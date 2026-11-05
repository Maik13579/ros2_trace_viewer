from importlib.metadata import distribution
from pathlib import Path
from xml.etree import ElementTree

import pytest


def test_source_frontend_matches_packaged_assets():
    root = Path(__file__).resolve().parents[1]
    sources = root / 'frontend' / 'src'
    for source in sources.iterdir():
        assert source.read_bytes() == (root / 'ros2_trace_viewer' / 'static' / source.name).read_bytes()


def test_installed_frontend_and_ros_entry_points():
    from ament_index_python.packages import get_package_prefix, get_package_share_directory
    try:
        prefix = Path(get_package_prefix('ros2_trace_viewer'))
    except LookupError:
        pytest.skip('Build and source ros2_trace_viewer to check installed assets')
    installed = list((prefix / 'lib').glob('python*/site-packages/ros2_trace_viewer/static'))
    assert len(installed) == 1
    expected = {'index.html','style.css','app.js','shared.js','tree.js','timeline.js','statistics.js','boxplots.js','topics.js','services.js','timers.js'}
    assert expected <= {p.name for p in installed[0].iterdir()}
    source = Path(__file__).resolve().parents[1] / 'ros2_trace_viewer' / 'static'
    for name in expected:
        assert (source / name).read_bytes() == (installed[0] / name).read_bytes()
    for executable in ['ros2_trace_viewer', 'ros2-trace-viewer']:
        assert (prefix / 'lib' / 'ros2_trace_viewer' / executable).is_file()
    manifest = ElementTree.parse(Path(get_package_share_directory('ros2_trace_viewer')) / 'package.xml')
    dependencies = {entry.text for entry in manifest.findall('exec_depend')}
    assert {'ros2trace','tracetools_analysis','tracetools_read','python3-babeltrace',
            'python3-numpy','python3-fastapi','python3-uvicorn'} <= dependencies
    test_dependencies = {entry.text for entry in manifest.findall('test_depend')}
    assert {'python3-httpx','python3-requests'} <= test_dependencies
