"""Offline check that the LangGraph local-agent graph still compiles.

create_local_agent_graph() only wires nodes/edges — no Ollama call, no tool
execution. This catches wiring regressions (bad node name, broken conditional
edge, node import failure) that would otherwise only surface the first time
someone drives the agent through a real query.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.local_agent_graph import create_local_agent_graph, get_local_agent_graph


def test_graph_compiles():
    graph = create_local_agent_graph()
    assert graph is not None


def test_graph_has_agent_and_tools_nodes():
    graph = create_local_agent_graph()
    node_names = set(graph.get_graph().nodes)
    assert "agent" in node_names
    assert "tools" in node_names


def test_get_local_agent_graph_returns_singleton():
    g1 = get_local_agent_graph()
    g2 = get_local_agent_graph()
    assert g1 is g2


if __name__ == "__main__":
    test_graph_compiles()
    test_graph_has_agent_and_tools_nodes()
    test_get_local_agent_graph_returns_singleton()
    print("OK — local-agent graph compiles and wiring is intact.")
