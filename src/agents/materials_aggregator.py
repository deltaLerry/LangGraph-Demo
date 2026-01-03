from __future__ import annotations

from typing import Any, Dict

from state import StoryState
from materials import build_materials_bundle, ensure_characters, ensure_outline, ensure_tone, ensure_world


def materials_aggregator_agent(state: StoryState) -> StoryState:
    """
    阶段3：材料包汇总器
    - 合并 4 个专家输出（world/characters/outline/tone）
    - 补默认/清洗结构
    - 生成 materials_bundle（写手只吃这一份）
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="materials_aggregator", chapter_index=0)

    planner_result = state.get("planner_result") or {}
    project_name = str((planner_result or {}).get("项目名称", "") or "")
    idea = str(state.get("user_input", "") or "")

    world_raw = state.get("architect_result") or {}
    chars_raw = state.get("character_director_result") or {}
    outline_raw = state.get("screenwriter_result") or {}
    tone_raw = state.get("tone_result") or {}

    # 清洗（保证结构稳定）
    world = ensure_world(world_raw)
    characters = ensure_characters(chars_raw)
    outline = ensure_outline(outline_raw)
    tone = ensure_tone(tone_raw)

    bundle = build_materials_bundle(
        project_name=project_name,
        idea=idea,
        world=world,
        characters=characters,
        outline=outline,
        tone=tone,
    )
    state["materials_bundle"] = bundle
    state["materials_used_llm"] = bool(
        state.get("architect_used_llm", False)
        or state.get("character_director_used_llm", False)
        or state.get("screenwriter_used_llm", False)
        or state.get("tone_used_llm", False)
    )
    if logger:
        logger.event(
            "node_end",
            node="materials_aggregator",
            chapter_index=0,
            used_llm=bool(state.get("materials_used_llm", False)),
        )
    return state


