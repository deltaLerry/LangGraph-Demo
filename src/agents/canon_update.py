from __future__ import annotations

from typing import Any, Dict, List

from state import StoryState


def canon_update_agent(state: StoryState) -> StoryState:
    """
    阶段2：Canon 增量更新（从“chapter memory”提炼为补丁建议）。

    最新设计原则（更安全、更可控）：
    - 本节点**不直接写 Canon**，只生成 `canon_update_suggestions`（补丁建议）
    - 建议落盘后，必须由用户通过 CLI “预览→确认→应用” 才会真正写入 Canon
    """
    logger = state.get("logger")
    chapter_index = int(state.get("chapter_index", 1))
    if logger:
        logger.event("node_start", node="canon_update", chapter_index=chapter_index)

    # 注意：canon_update_suggestions 是“建议层”，不直接修改 Canon；
    # 为了保持落盘章节的一致性（即使审核不通过/达到返工上限），此处不再以 editor_decision 作为门控条件。
    #
    # 数据治理门禁（默认更安全）：仅在“审核通过”时产出沉淀建议。
    # 如你确实要在不通过时也产出建议，请在 state 中设置 allow_unapproved_updates=True。

    project_dir = str(state.get("project_dir", "") or "")
    if not project_dir:
        state["canon_update_used"] = False
        state["canon_update_suggestions"] = []
        if logger:
            logger.event("node_end", node="canon_update", chapter_index=chapter_index, skipped=True, reason="missing_project_dir")
        return state

    mem = state.get("chapter_memory") or {}
    if not isinstance(mem, dict) or not mem:
        state["canon_update_used"] = False
        state["canon_update_suggestions"] = []
        if logger:
            logger.event("node_end", node="canon_update", chapter_index=chapter_index, skipped=True, reason="missing_chapter_memory")
        return state

    # 生成建议（统一复用 apply_canon_suggestions 支持的最保守 op：note / append）
    suggestions: List[Dict[str, Any]] = []
    editor_decision = str(state.get("editor_decision", "") or "").strip()
    approved = editor_decision == "审核通过"
    if (not approved) and (not bool(state.get("allow_unapproved_updates", False))):
        state["canon_update_used"] = False
        state["canon_update_suggestions"] = []
        if logger:
            logger.event("node_end", node="canon_update", chapter_index=chapter_index, skipped=True, reason="not_approved")
        return state

    # 1) new_facts：写入 world.json notes（最安全，避免 schema/定位复杂）
    new_facts = mem.get("new_facts") if isinstance(mem.get("new_facts"), list) else []
    for it in new_facts:
        if not isinstance(it, dict):
            continue
        t = str(it.get("type", "") or "").strip()
        k = str(it.get("key", "") or "").strip()
        v = str(it.get("value", "") or "").strip()
        if not (t and k and v):
            continue
        line = f"[第{chapter_index}章][{t}] {k}：{v}"
        suggestions.append(
            {
                "source": "memory",
                "chapter_index": chapter_index,
                "editor_decision": editor_decision,
                "approved": approved,
                "action": "canon_patch",
                "issue": f"沉淀 chapter memory 的 new_facts 到 Canon（{t}）",
                "quote": "",
                "type": "world",
                "canon_key": "world.notes",
                "fix": "追加到 world.notes，供后续人工结构化整理",
                "canon_patch": {"target": "world.json", "op": "note", "path": "notes", "value": line},
            }
        )

    # 2) character_updates：同样先写入 world.notes（避免直接改人物卡导致误伤）
    cu = mem.get("character_updates") if isinstance(mem.get("character_updates"), list) else []
    for it in cu:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "") or "").strip()
        status = str(it.get("status", "") or "").strip()
        new_info = str(it.get("new_info", "") or "").strip()
        if not name or (not status and not new_info):
            continue
        line = f"[第{chapter_index}章][角色更新] {name}：{status}；{new_info}".strip("； ").strip()
        suggestions.append(
            {
                "source": "memory",
                "chapter_index": chapter_index,
                "editor_decision": editor_decision,
                "approved": approved,
                "action": "canon_patch",
                "issue": "沉淀角色状态变更到 Canon（先记 notes，后续可再结构化进人物卡）",
                "quote": "",
                "type": "character",
                "canon_key": "world.notes",
                "fix": "追加到 world.notes",
                "canon_patch": {"target": "world.json", "op": "note", "path": "notes", "value": line},
            }
        )

    # 3) style_notes：追加到 style.md
    style_notes = mem.get("style_notes") if isinstance(mem.get("style_notes"), list) else []
    for s in [str(x).strip() for x in style_notes if str(x).strip()]:
        suggestions.append(
            {
                "source": "memory",
                "chapter_index": chapter_index,
                "editor_decision": editor_decision,
                "approved": approved,
                "action": "canon_patch",
                "issue": "沉淀文风要点到 style.md",
                "quote": "",
                "type": "style",
                "canon_key": "style.md",
                "fix": "追加 bullet 到 style.md",
                "canon_patch": {"target": "style.md", "op": "append", "path": "N/A", "value": s},
            }
        )

    state["canon_update_suggestions"] = suggestions
    state["canon_update_used"] = bool(suggestions)
    if logger:
        logger.event(
            "node_end",
            node="canon_update",
            chapter_index=chapter_index,
            used=bool(state.get("canon_update_used", False)),
            suggestions_count=len(suggestions),
        )
    return state


