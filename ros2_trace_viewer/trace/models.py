"""SQLite schema. Times are signed integer nanoseconds relative to trace origin."""
import sqlite3

SCHEMA = '''
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
PRAGMA cache_size=-32768;
PRAGMA temp_store=FILE;
CREATE TABLE processes(pid INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE nodes(id TEXT PRIMARY KEY, pid INTEGER, name TEXT);
CREATE TABLE callbacks(id TEXT PRIMARY KEY, pid INTEGER, pointer TEXT,
                       node_id TEXT, type TEXT, symbol TEXT, initialized INTEGER, topic TEXT,
                       service TEXT, timer_period_ns INTEGER);
CREATE TABLE endpoints(id TEXT PRIMARY KEY, pid INTEGER, kind TEXT, node_id TEXT,
                       topic TEXT, handle TEXT, rmw_handle TEXT, queue_depth INTEGER,
                       created_ns INTEGER);
CREATE TABLE service_endpoints(id TEXT PRIMARY KEY, pid INTEGER, kind TEXT, node_id TEXT,
                               name TEXT, handle TEXT, rmw_handle TEXT, queue_depth INTEGER,
                               created_ns INTEGER);
CREATE TABLE publications(id INTEGER PRIMARY KEY, scope_id INTEGER, execution_id INTEGER,
                          pid INTEGER, tid INTEGER, timestamp_ns INTEGER, message_pointer TEXT,
                          endpoint_id TEXT, node_id TEXT, topic TEXT, layers INTEGER,
                          source_timestamp INTEGER);
CREATE INDEX publications_scopes ON publications(scope_id);
CREATE TABLE publication_counts(execution_id INTEGER PRIMARY KEY, count INTEGER);
CREATE TABLE topic_receives(topic TEXT, pid INTEGER, tid INTEGER, node_id TEXT,
                           timestamp_ns INTEGER);
CREATE TABLE executions(id INTEGER PRIMARY KEY, callback_id TEXT, pid INTEGER,
                        tid INTEGER, host_tid INTEGER, start_ns INTEGER,
                        end_ns INTEGER, duration_ns INTEGER,
                        start_cpu INTEGER, end_cpu INTEGER);
CREATE TABLE execution_topics(execution_id INTEGER, topic TEXT,
                             subscription INTEGER, publish_count INTEGER,
                             PRIMARY KEY(execution_id,topic));
CREATE TABLE action_channels(transport TEXT, name TEXT, action TEXT, role TEXT,
                             PRIMARY KEY(transport,name));
CREATE TABLE execution_actions(execution_id INTEGER, action TEXT,
                               PRIMARY KEY(execution_id,action));
CREATE VIRTUAL TABLE intervals USING rtree(id, start_s, end_s);
CREATE TABLE scheduler(tid INTEGER, cpu INTEGER, start_ns INTEGER, end_ns INTEGER);
CREATE TABLE cpu_segments(id INTEGER PRIMARY KEY, execution_id INTEGER,
                          cpu INTEGER, start_ns INTEGER, end_ns INTEGER);
CREATE VIRTUAL TABLE cpu_intervals USING rtree(id, start_s, end_s);
CREATE TABLE info(key TEXT PRIMARY KEY, value TEXT);
'''

INDICES = '''
CREATE INDEX callback_executions ON executions(callback_id, start_ns, id);
CREATE INDEX execution_threads ON executions(pid, tid);
CREATE INDEX scheduler_threads ON scheduler(tid, start_ns, end_ns);
CREATE INDEX segment_executions ON cpu_segments(execution_id);
CREATE INDEX callback_nodes ON callbacks(node_id);
CREATE INDEX publications_executions ON publications(execution_id,timestamp_ns,id);
CREATE INDEX publications_times ON publications(timestamp_ns);
CREATE INDEX publications_topics ON publications(topic,timestamp_ns);
CREATE INDEX receives_topics ON topic_receives(topic,timestamp_ns);
CREATE INDEX endpoint_topics ON endpoints(topic,kind);
CREATE INDEX endpoint_services ON service_endpoints(name,kind);
CREATE INDEX execution_topic_lanes ON execution_topics(topic,execution_id);
CREATE INDEX action_channel_names ON action_channels(action,transport,name);
CREATE INDEX action_executions ON execution_actions(action,execution_id);
ANALYZE;
'''


def create_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    return connection
