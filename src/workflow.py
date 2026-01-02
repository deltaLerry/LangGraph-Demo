from __future__ import annotations

from langgraph.graph import StateGraph, END

from state import StoryState
from agents.writer import writer_agent
from agents.editor import editor_agent
from agents.memory import memory_agent
from agents.canon_update import canon_update_agent


def _next_step_after_editor(state: StoryState):
    needs_rewrite = bool(state.get("needs_rewrite", False))
    if not needs_rewrite:
        return "memory"

    writer_version = int(state.get("writer_version", 1))
    max_rewrites = int(state.get("max_rewrites", 1))
    if writer_version < 1 + max_rewrites:
        return "writer"
    # 达到返工次数上限：仍然进入 memory（沉淀本章记忆），再由后续节点自行决定是否做设定沉淀。
    return "memory"


def build_chapter_app():
    """
    章节子工作流：写手 -> 主编（不通过则返工到写手，最多 max_rewrites 次）

    用于“策划一次 + 多章节循环”的场景。
    """
    graph = StateGraph(StoryState)
    graph.add_node("writer", writer_agent)
    graph.add_node("editor", editor_agent)
    graph.add_node("memory", memory_agent)
    graph.add_node("canon_update", canon_update_agent)

    graph.set_entry_point("writer")
    graph.add_edge("writer", "editor")
    graph.add_conditional_edges("editor", _next_step_after_editor, {"writer": "writer", "memory": "memory", END: END})
    graph.add_edge("memory", "canon_update")
    graph.add_edge("canon_update", END)
    return graph.compile()
