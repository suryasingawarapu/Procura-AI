"""Supervisor-controlled multi-agent workflow with planned roles."""

from __future__ import annotations

from typing import Any, Dict, TypedDict

from langgraph.graph import END, START, StateGraph


ROLES = ["supervisor", "planner", "researcher", "executor", "validator"]

MAX_RETRIES = 2


class AgentState(TypedDict, total=False):
    task: str
    messages: list
    current_agent: str
    context: list
    result: str
    plan: list
    step: int
    retries: int
    validation_passed: bool
    failed: bool
    error: str


def route_task(state: AgentState) -> str:
    """Route tasks to appropriate agents based on role."""
    task = state.get("task", "").lower()
    failed = state.get("failed", False)
    if failed:
        return "supervisor"
    
    if "plan" in task or "strategy" in task:
        return "planner"
    elif "research" in task or "search" in task or "find" in task:
        return "researcher"
    elif "execute" in task or "implement" in task or "fail" in task:
        return "executor"
    elif "validate" in task or "check" in task or "verify" in task:
        return "validator"
    elif "do" in task and not ("something unclear" in task or "unclear" in task) and "fail" not in task:
        return "executor"
    else:
        return "supervisor"


def supervisor_node(state: AgentState) -> AgentState:
    step = state.get("step", 0) + 1
    retries = state.get("retries", 0)
    
    if state.get("failed", False):
        if retries >= MAX_RETRIES:
            return {
                "current_agent": "supervisor",
                "result": f"Supervisor: Max retries reached, aborting. Error: {state.get('error', 'unknown')}",
                "failed": True,
            }
        return {
            "current_agent": "supervisor",
            "result": f"Supervisor: Retrying failed step (attempt {retries + 1}/{MAX_RETRIES + 1})",
            "retries": retries + 1,
            "failed": False,
        }
    
    context = state.get("context", [])
    return {
        "current_agent": "supervisor",
        "result": f"Supervisor coordinating step {step} with {len(context)} context items",
        "step": step,
        "context": context,
    }


def planner_node(state: AgentState) -> AgentState:
    context = state.get("context", [])
    plan = state.get("plan", []) or ["research", "execute", "validate"]
    return {
        "current_agent": "planner",
        "result": f"Planner created plan: {', '.join(plan)}",
        "plan": plan,
        "context": context,
    }


def researcher_node(state: AgentState) -> AgentState:
    context = state.get("context", []) + [{"type": "research", "agent": "researcher"}]
    return {
        "current_agent": "researcher",
        "result": f"Researcher gathered info for: {state.get('task', '')}",
        "context": context,
    }


def executor_node(state: AgentState) -> AgentState:
    context = state.get("context", []) + [{"type": "execution", "agent": "executor"}]
    # Simulate possible failure
    if "fail" in state.get("task", "").lower():
        return {
            "current_agent": "executor",
            "result": "Executor failed to complete task",
            "context": context,
            "failed": True,
            "error": "Execution failed",
        }
    return {
        "current_agent": "executor",
        "result": f"Executor completed: {state.get('task', '')}",
        "context": context,
    }


def validator_node(state: AgentState) -> AgentState:
    retries = state.get("retries", 0)
    # Simulate validation
    if "invalid" in state.get("task", "").lower() and retries < MAX_RETRIES:
        return {
            "current_agent": "validator",
            "result": "Validator found issues, requesting retry",
            "validation_passed": False,
            "failed": True,
            "error": "Validation failed",
            "retries": retries,
        }
    
    return {
        "current_agent": "validator",
        "result": "Validator approved the result",
        "validation_passed": True,
    }


def should_retry(state: AgentState) -> str:
    """Determine if we should retry after failure."""
    if state.get("failed", False) and state.get("retries", 0) < MAX_RETRIES:
        return "supervisor"
    elif state.get("failed", False):
        return END
    elif state.get("validation_passed") is False:
        return "supervisor"
    else:
        return END


def build_workflow():
    """Build the multi-agent workflow graph."""
    graph = StateGraph(AgentState)
    
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("planner", planner_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("executor", executor_node)
    graph.add_node("validator", validator_node)
    
    graph.add_conditional_edges(
        START,
        route_task,
        {
            "supervisor": "supervisor",
            "planner": "planner",
            "researcher": "researcher",
            "executor": "executor",
            "validator": "validator",
        },
    )
    
    graph.add_edge("supervisor", END)
    graph.add_edge("planner", "supervisor")
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("executor", "validator")
    graph.add_conditional_edges(
        "validator",
        should_retry,
        {
            "supervisor": "supervisor",
            END: END,
        },
    )
    
    return graph.compile()

