"""The first LangGraph workflow above the existing brain layer."""

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .router import route_request


class SheilaWorkflowState(TypedDict, total=False):
    user_text: str
    route: dict[str, object]
    response_handler: Callable[[str], str]
    response: str


def routing_node(state: SheilaWorkflowState) -> dict[str, object]:
    """Classify the request without invoking a specialist."""
    return {"route": route_request(state["user_text"]).to_dict()}


def sheila_response_node(state: SheilaWorkflowState) -> dict[str, str]:
    """Use the established brain layer while planned specialists are unavailable."""
    decision = state["route"]
    response = state["response_handler"](state["user_text"])
    if decision["agent"] != "Sheila":
        response = (
            f"I've identified {decision['agent']} as the right future specialist for this. "
            f"Until that agent is implemented, I'll handle it myself. {response}"
        )
    return {"response": response}


def build_workflow():
    """Build the minimal request -> route -> Sheila fallback -> final graph."""
    graph = StateGraph(SheilaWorkflowState)
    graph.add_node("route", routing_node)
    graph.add_node("sheila_response", sheila_response_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "sheila_response")
    graph.add_edge("sheila_response", END)
    return graph.compile()


_workflow = build_workflow()


def handle_request(user_text: str, response_handler: Callable[[str], str]) -> dict[str, object]:
    """Run a conversational request through Sheila's orchestration layer.

    ``response_handler`` is supplied by the caller so ``brain.py`` remains the
    only model/fallback layer.
    """
    return _workflow.invoke({"user_text": user_text, "response_handler": response_handler})
