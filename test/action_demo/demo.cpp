// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Maik Knof
#include <chrono>
#include <functional>
#include <future>
#include <mutex>
#include <queue>
#include <thread>
#include <example_interfaces/action/fibonacci.hpp>
#include <example_interfaces/srv/add_two_ints.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <std_msgs/msg/int64.hpp>
#include "action_trace.hpp"
using namespace std::chrono_literals;
using Fibonacci=example_interfaces::action::Fibonacci;
using Add=example_interfaces::srv::AddTwoInts;
using ServerGoal=rclcpp_action::ServerGoalHandle<Fibonacci>;
using ClientGoal=rclcpp_action::ClientGoalHandle<Fibonacci>;

class Sensor:public rclcpp::Node
{
public:
  Sensor():Node("sensor") {
    publisher_=create_publisher<std_msgs::msg::Int64>("/viewer_demo/input",10);
    timer_=create_wall_timer(250ms,[this]() {
      std_msgs::msg::Int64 message;message.data=++sequence_;publisher_->publish(message);
    });
  }
private:
  int64_t sequence_=0;
  rclcpp::Publisher<std_msgs::msg::Int64>::SharedPtr publisher_;
  rclcpp::TimerBase::SharedPtr timer_;
};

class ProcessorNode:public rclcpp::Node
{
public:
  ProcessorNode():Node("processor") {
    publisher_=create_publisher<std_msgs::msg::Int64>("/viewer_demo/output",10);
    subscription_=create_subscription<std_msgs::msg::Int64>("/viewer_demo/input",10,
      [this](std_msgs::msg::Int64::ConstSharedPtr input) {
        std::this_thread::sleep_for(std::chrono::milliseconds(2+input->data%4));
        auto output=*input;output.data*=2;publisher_->publish(output);
      });
  }
private:
  rclcpp::Publisher<std_msgs::msg::Int64>::SharedPtr publisher_;
  rclcpp::Subscription<std_msgs::msg::Int64>::SharedPtr subscription_;
};

class DemoServer:public rclcpp::Node
{
public:
  DemoServer():Node("action_server") {
    action_=declare_parameter<std::string>("action_name","/viewer_demo/fibonacci");
    goal_=std::make_unique<ActionCallback>(*this,action_,"goal","server");
    cancel_=std::make_unique<ActionCallback>(*this,action_,"cancel","server");
    accepted_=std::make_unique<ActionCallback>(*this,action_,"accepted","server");
    execute_=std::make_unique<ActionCallback>(*this,action_,"execute","server");
    service_=create_service<Add>("/viewer_demo/add_two_ints",
      [](const Add::Request::SharedPtr request,Add::Response::SharedPtr response) {
        // Deliberate blocking work is part of the actual measured duration.
        std::this_thread::sleep_for(std::chrono::milliseconds(25+(request->a%4)*10));
        response->sum=request->a+request->b;
      });
    server_=rclcpp_action::create_server<Fibonacci>(this,action_,
      [this](const rclcpp_action::GoalUUID &,std::shared_ptr<const Fibonacci::Goal> goal) {
        CallbackScope scope(*goal_);
        if(goal->order<2||goal->order>25||busy_.exchange(true))return rclcpp_action::GoalResponse::REJECT;
        return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
      },[this](std::shared_ptr<ServerGoal>) {
        CallbackScope scope(*cancel_);return rclcpp_action::CancelResponse::ACCEPT;
      },[this](std::shared_ptr<ServerGoal> handle) {
        CallbackScope scope(*accepted_);
        if(worker_.joinable())worker_.join();
        worker_=std::thread([this,handle]() {execute(handle);});
      });
  }
  ~DemoServer() override {if(worker_.joinable())worker_.join();}
private:
  void execute(const std::shared_ptr<ServerGoal> & handle) {
    CallbackScope scope(*execute_);
    auto feedback=std::make_shared<Fibonacci::Feedback>();
    auto result=std::make_shared<Fibonacci::Result>();feedback->sequence={0,1};
    for(int i=1;i<handle->get_goal()->order&&rclcpp::ok();++i) {
      if(handle->is_canceling()) {
        result->sequence=feedback->sequence;handle->canceled(result);busy_=false;
        RCLCPP_INFO(get_logger(),"Goal cancelled");return;
      }
      auto & sequence=feedback->sequence;
      sequence.push_back(sequence[sequence.size()-1]+sequence[sequence.size()-2]);
      handle->publish_feedback(feedback);
      std::this_thread::sleep_for(std::chrono::milliseconds(70+(i%3)*15));
    }
    if(rclcpp::ok()) {
      result->sequence=feedback->sequence;handle->succeed(result);
      RCLCPP_INFO(get_logger(),"Goal succeeded");
    }
    busy_=false;
  }
  std::string action_;
  std::atomic<bool> busy_{false};
  std::thread worker_;
  std::unique_ptr<ActionCallback> goal_,cancel_,accepted_,execute_;
  rclcpp_action::Server<Fibonacci>::SharedPtr server_;
  rclcpp::Service<Add>::SharedPtr service_;
};

class DemoClient:public rclcpp::Node
{
public:
  DemoClient():Node("action_client") {
    action_=declare_parameter<std::string>("action_name","/viewer_demo/fibonacci");
    response_=std::make_unique<ActionCallback>(*this,action_,"goal_response","client");
    feedback_=std::make_unique<ActionCallback>(*this,action_,"feedback","client");
    result_=std::make_unique<ActionCallback>(*this,action_,"result","client");
    client_=rclcpp_action::create_client<Fibonacci>(this,action_);
    // Distinct response group allows replies while the timer callback waits.
    service_group_=create_callback_group(rclcpp::CallbackGroupType::Reentrant);
    service_=create_client<Add>("/viewer_demo/add_two_ints",rclcpp::ServicesQoS(),service_group_);
    subscription_=create_subscription<std_msgs::msg::Int64>("/viewer_demo/output",10,
      [this](std_msgs::msg::Int64::ConstSharedPtr message) {last_sample_=message->data;});
    timer_=create_wall_timer(100ms,[this]() {
      timer_->cancel();std::function<void()> job;
      {std::lock_guard<std::mutex> lock(mutex_);if(!jobs_.empty()){job=jobs_.front();jobs_.pop();}}
      if(!job)return;
      auto request=std::make_shared<Add::Request>();request->a=++requests_;request->b=2;
      auto future=service_->async_send_request(request);
      const bool waits=requests_%2==1;
      const auto call=trace_call(*this,"/viewer_demo/add_two_ints",future.request_id,waits);
      if(waits) {
        if(future.wait_for(3s)!=std::future_status::ready)throw std::runtime_error("Service timed out");
        if(future.get()->sum!=request->a+request->b)throw std::runtime_error("Wrong response");
        tracepoint(ros2_trace_viewer,client_call_end,call);
        RCLCPP_INFO(get_logger(),"Blocking service reply received");
      }
      job();
    });timer_->cancel();
  }
  void run() {
    if(!client_->wait_for_action_server(5s)||!service_->wait_for_service(5s))throw std::runtime_error("Server unavailable");
    for(const int order:{5,7,9,6})goal(order,false);
    goal(20,true);goal(0,false);RCLCPP_INFO(get_logger(),"ALL_SCENARIOS_PASSED");
  }
private:
  void post(std::function<void()> job) {
    {std::lock_guard<std::mutex> lock(mutex_);jobs_.push(std::move(job));}timer_->reset();
  }
  void goal(int order,bool cancel) {
    auto accepted=std::make_shared<std::promise<ClientGoal::SharedPtr>>();
    auto done=std::make_shared<std::promise<rclcpp_action::ResultCode>>();
    auto handle_future=accepted->get_future();auto outcome=done->get_future();feedback_count_=0;
    post([this,order,accepted,done]() {
      rclcpp_action::Client<Fibonacci>::SendGoalOptions options;
      options.goal_response_callback=[this,accepted,done](ClientGoal::SharedPtr handle) {
        CallbackScope scope(*response_);
        if(handle) {
          trace_call(*this,action_+"/_action/get_result");
          client_->async_get_result(handle,[this,done](const ClientGoal::WrappedResult & result) {
            CallbackScope scope(*result_);done->set_value(result.code);
          });
        }
        accepted->set_value(handle);
      };
      options.feedback_callback=[this](ClientGoal::SharedPtr,const std::shared_ptr<const Fibonacci::Feedback> feedback) {
        CallbackScope scope(*feedback_);last_sample_=feedback->sequence.back();++feedback_count_;
      };
      Fibonacci::Goal request;request.order=order;
      trace_call(*this,action_+"/_action/send_goal");client_->async_send_goal(request,options);
    });
    if(handle_future.wait_for(5s)!=std::future_status::ready)throw std::runtime_error("Goal response timed out");
    auto handle=handle_future.get();
    if(order==0) {
      if(handle)throw std::runtime_error("Invalid goal accepted");
      RCLCPP_INFO(get_logger(),"Invalid goal rejected");return;
    }
    if(!handle)throw std::runtime_error("Valid goal rejected");
    if(cancel) {
      const auto deadline=std::chrono::steady_clock::now()+5s;
      while(feedback_count_<4&&std::chrono::steady_clock::now()<deadline)std::this_thread::sleep_for(10ms);
      if(feedback_count_<4)throw std::runtime_error("Feedback timed out");
      post([this,handle]() {trace_call(*this,action_+"/_action/cancel_goal");client_->async_cancel_goal(handle);});
    }
    if(outcome.wait_for(8s)!=std::future_status::ready||outcome.get()!=
      (cancel?rclcpp_action::ResultCode::CANCELED:rclcpp_action::ResultCode::SUCCEEDED))throw std::runtime_error("Wrong outcome");
    std::this_thread::sleep_for(80ms);
  }
  std::string action_;
  std::atomic<int> feedback_count_{0};
  std::atomic<int64_t> last_sample_{0};
  int64_t requests_=0;
  std::mutex mutex_;
  std::queue<std::function<void()>> jobs_;
  std::unique_ptr<ActionCallback> response_,feedback_,result_;
  rclcpp_action::Client<Fibonacci>::SharedPtr client_;
  rclcpp::CallbackGroup::SharedPtr service_group_;
  rclcpp::Client<Add>::SharedPtr service_;
  rclcpp::Subscription<std_msgs::msg::Int64>::SharedPtr subscription_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc,char ** argv)
{
  rclcpp::init(argc,argv);const bool server=argc>1&&std::string(argv[1])=="server";
  rclcpp::executors::MultiThreadedExecutor executor(rclcpp::ExecutorOptions(),3);int result=0;
  if(server) {
    auto action=std::make_shared<DemoServer>();auto sensor=std::make_shared<Sensor>();
    auto processor=std::make_shared<ProcessorNode>();
    executor.add_node(action);executor.add_node(sensor);executor.add_node(processor);executor.spin();
  }else {
    auto client=std::make_shared<DemoClient>();executor.add_node(client);
    std::thread spin([&executor]() {executor.spin();});
    try{client->run();}catch(const std::exception & error){RCLCPP_ERROR(client->get_logger(),"%s",error.what());result=1;}
    executor.cancel();spin.join();
  }
  rclcpp::shutdown();return result;
}
