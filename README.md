# ROS 2 Trace Viewer

A standalone offline viewer for finished ROS 2 LTTng CTF recordings.
The browser shows callback executions on a **vertical timeline** and aggregates
callback durations in a statistics view.

## Start

```bash
ros2 run ros2_trace_viewer ros2_trace_viewer \
  --trace-dir [path to trace dir] --host 0.0.0.0 --port 8080
```
