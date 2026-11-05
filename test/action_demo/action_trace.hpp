// SPDX-License-Identifier: Apache-2.0
// Explicit application instrumentation; these are actual invocation boundaries.
#pragma once
#include <atomic>
#include <cstdint>
#include <string>
#include <rclcpp/rclcpp.hpp>
#include <tracetools/tracetools.h>
#include "action_provider.h"

class ActionCallback
{
public:
  ActionCallback(rclcpp::Node & node,const std::string & action,const char * role,const char * side)
  {
    const auto symbol=std::string("action_")+side+"::"+role;
    tracepoint(ros2_trace_viewer,action_callback_init,&identity_,
      node.get_node_base_interface()->get_rcl_node_handle(),action.c_str(),role,side,symbol.c_str());
  }
  const void * identity() const {return &identity_;}
  ActionCallback(const ActionCallback &)=delete;
  ActionCallback & operator=(const ActionCallback &)=delete;
private:
  uint8_t identity_=0;
};

class CallbackScope
{
public:
  explicit CallbackScope(const ActionCallback & callback):identity_(callback.identity())
  {TRACETOOLS_TRACEPOINT(callback_start,identity_,false);}
  ~CallbackScope() {TRACETOOLS_TRACEPOINT(callback_end,identity_);}
  CallbackScope(const CallbackScope &)=delete;
  CallbackScope & operator=(const CallbackScope &)=delete;
private:
  const void * identity_;
};

inline uint64_t trace_call(rclcpp::Node & node,const std::string & service,
  int64_t request_id=-1,bool waits=false)
{
  static std::atomic<uint64_t> next{1};const auto id=next.fetch_add(1);
  tracepoint(ros2_trace_viewer,client_call,id,node.get_node_base_interface()->get_rcl_node_handle(),
    service.c_str(),request_id,static_cast<int>(waits));return id;
}
