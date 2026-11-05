// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Maik Knof
#undef TRACEPOINT_PROVIDER
#define TRACEPOINT_PROVIDER ros2_trace_viewer
#undef TRACEPOINT_INCLUDE
#define TRACEPOINT_INCLUDE "action_provider.h"
#if !defined(VIEWER_ACTION_PROVIDER_H) || defined(TRACEPOINT_HEADER_MULTI_READ)
#define VIEWER_ACTION_PROVIDER_H
#include <lttng/tracepoint.h>
#include <stdint.h>
TRACEPOINT_EVENT(ros2_trace_viewer, action_callback_init,
  TP_ARGS(const void *, callback_arg, const void *, node_arg,
    const char *, action_arg, const char *, role_arg, const char *, side_arg,
    const char *, symbol_arg),
  TP_FIELDS(ctf_integer_hex(const void *, callback, callback_arg)
    ctf_integer_hex(const void *, node_handle, node_arg)
    ctf_string(action_name, action_arg) ctf_string(role, role_arg)
    ctf_string(side, side_arg) ctf_string(symbol, symbol_arg)))
TRACEPOINT_EVENT(ros2_trace_viewer, client_call,
  TP_ARGS(uint64_t, id_arg, const void *, node_arg, const char *, service_arg,
    int64_t, request_arg, int, wait_arg),
  TP_FIELDS(ctf_integer(uint64_t, call_id, id_arg)
    ctf_integer_hex(const void *, node_handle, node_arg)
    ctf_string(service_name, service_arg) ctf_integer(int64_t, request_id, request_arg)
    ctf_integer(int, wait_for_response, wait_arg)))
TRACEPOINT_EVENT(ros2_trace_viewer, client_call_end,
  TP_ARGS(uint64_t, id_arg), TP_FIELDS(ctf_integer(uint64_t, call_id, id_arg)))
#endif
#include <lttng/tracepoint-event.h>
