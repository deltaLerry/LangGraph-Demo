from __future__ import annotations

from langgraph.graph import StateGraph, END

from state import StoryState
from agents.planner import planner_agent
from agents.writer import writer_agent
from agents.editor import editor_agent
from agents.memory import memory_agent


def _next_step_after_editor(state: StoryState):
    needs_rewrite = bool(state.get("needs_rewrite", False))
    if not needs_rewrite:
        return "memory"

    writer_version = int(state.get("writer_version", 1))
    max_rewrites = int(state.get("max_rewrites", 1))
    if writer_version < 1 + max_rewrites:
        return "writer"
    return END


def build_chapter_app():
    """
    章节子工作流：写手 -> 主编（不通过则返工到写手，最多 max_rewrites 次）

    用于“策划一次 + 多章节循环”的场景。
    """
    graph = StateGraph(StoryState)
    graph.add_node("writer", writer_agent)
    graph.add_node("editor", editor_agent)
    graph.add_node("memory", memory_agent)

    graph.set_entry_point("writer")
    graph.add_edge("writer", "editor")
    graph.add_conditional_edges("editor", _next_step_after_editor, {"writer": "writer", "memory": "memory", END: END})
    graph.add_edge("memory", END)
    return graph.compile()


def build_app():
    """
    MVP 工作流：策划 -> 写手 -> 主编（不通过则返工到写手，最多 max_rewrites 次）
    """
    graph = StateGraph(StoryState)
    graph.add_node("planner", planner_agent)
    graph.add_node("writer", writer_agent)
    graph.add_node("editor", editor_agent)
    graph.add_node("memory", memory_agent)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "writer")
    graph.add_edge("writer", "editor")
    graph.add_conditional_edges("editor", _next_step_after_editor, {"writer": "writer", "memory": "memory", END: END})
    graph.add_edge("memory", END)

    return graph.compile()

