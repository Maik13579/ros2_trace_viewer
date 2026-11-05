#!/usr/bin/env python3
"""Package native ES modules and CSS. No npm, Node, or network is needed."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
for source in (root / 'src').iterdir():
    if source.is_file():
        shutil.copyfile(source, root.parent / 'ros2_trace_viewer' / 'static' / source.name)
