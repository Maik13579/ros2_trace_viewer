"""Read CTF using ros2_tracing; never create converted/pickle files in a trace."""
from contextlib import contextmanager
from pathlib import Path
import os
import tempfile

from .models import create_database
from .processor import Processor


class TraceError(ValueError):
    """Invalid, unsupported, unreadable, or empty trace input."""


def validate_trace_path(path):
    """Locate CTF metadata under a recording/session directory without writes."""
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise TraceError(f'Trace directory does not exist: {root}')
    if not root.is_dir():
        raise TraceError(f'Expected a trace directory, not a file: {root}')
    metadata = []

    def failed(error):
        raise TraceError(f'Cannot read trace directory: {error}')

    for directory, _, files in os.walk(root, onerror=failed, followlinks=False):
        if 'metadata' in files:
            candidate = Path(directory) / 'metadata'
            try:
                with candidate.open('rb') as stream:
                    if not stream.read(1):
                        raise TraceError(f'Empty CTF metadata: {candidate}')
            except OSError as error:
                raise TraceError(f'Cannot read CTF metadata: {error}') from error
            metadata.append(candidate.parent)
    if not metadata:
        raise TraceError(f'No LTTng CTF metadata found under {root}. '
                         'Pass the recorded session directory or its ust/kernel subdirectory.')
    return root, metadata


def read_events(root):
    """Yield normalized event dictionaries in timestamp order, with bounded memory.

    tracetools_analysis.load_file writes converted files into the input and
    materializes all events. Instead use its underlying tracetools_read iterator.
    The same upstream event conversion and CTF implementation are retained.
    """
    from tracetools_read.trace import event_to_dict, get_trace_ctf_events
    discarded = {}
    try:
        for event in get_trace_ctf_events(str(root)):
            result = event_to_dict(event)
            if 'events_discarded' in event.keys():
                key = (result.get('cpu_id'), result.get('stream_id'))
                total = int(event['events_discarded'])
                result['_discarded'] = max(0, total - discarded.get(key, 0))
                discarded[key] = total
            yield result
    except (RuntimeError, ValueError, OSError, TypeError) as error:
        raise TraceError(f'Cannot read LTTng CTF trace: {error}') from error


@contextmanager
def load_trace(path, progress=print, work_dir=None):
    """Build an ephemeral index outside the source and keep it alive until exit.

    No persistent cache means stale-cache invalidation is unnecessary. work_dir
    may specify a different scratch disk; it must be outside the input trace.
    """
    root, metadata = validate_trace_path(path)
    scratch = Path(work_dir or tempfile.gettempdir()).resolve()
    if scratch == root or root in scratch.parents:
        raise TraceError('The index work directory must be outside the read-only trace directory')
    progress(f'Loading {root.name}: {len(metadata)} CTF stream directorie(s)…')
    with tempfile.TemporaryDirectory(prefix='ros2-trace-viewer-', dir=scratch) as directory:
        database = Path(directory) / 'trace.sqlite3'
        db = create_database(database)
        try:
            processor = Processor(db)
            for event in read_events(root):
                processor.consume(event)
                if processor.counts['events'] % 100000 == 0:
                    progress(f"Processed {processor.counts['events']:,} events; "
                             f"matched {processor.counts['executions']:,} callbacks…")
            progress('Building interval and callback indices…')
            info = processor.finalize(root.name)
            if not info['execution_count']:
                raise TraceError('No complete ROS 2 callback executions found. Record '
                    'ros2:callback_start and ros2:callback_end with vpid/vtid contexts. '
                    f"Diagnostics: {info['diagnostics']}")
            if not info['known_execution_count']:
                raise TraceError('No initialized callbacks found. Include callback registration '
                                 'or callback association initialization events in the recording.')
            progress(f"Ready: {info['execution_count']:,} callback executions, "
                     f"{info['duration_seconds']:.6f} s")
        finally:
            db.close()
        yield database
