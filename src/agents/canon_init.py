from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from state import StoryState
from debug_log import truncate_text
import os

from storage import load_canon_bundle, read_json, write_json, write_text


def _is_placeholder_world(world: Dict[str, Any]) -> bool:
    if not isinstance(world, dict):
        return True
    return (
        (world.get("rules") in (None, [], ""))  # type: ignore[comparison-overlap]
        and (world.get("factions") in (None, [], ""))
        and (world.get("places") in (None, [], ""))
        and (str(world.get("notes", "") or "").strip() == "")
    )


def _is_placeholder_characters(characters: Dict[str, Any]) -> bool:
    if not isinstance(characters, dict):
        return True
    arr = characters.get("characters")
    return not isinstance(arr, list) or len(arr) == 0


def _is_placeholder_timeline(timeline: Dict[str, Any]) -> bool:
    if not isinstance(timeline, dict):
        return True
    arr = timeline.get("events")
    return not isinstance(arr, list) or len(arr) == 0


def _merge_keep_existing(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """
    轻量合并：existing 有值则保留；否则用 new。
    仅做一层 dict 合并，避免复杂冲突处理（后续由专门的设定更新器/主编来做）。
    """
    out = dict(existing or {})
    for k, v in (new or {}).items():
        if k not in out or out.get(k) in (None, "", [], {}):
            out[k] = v
    return out


def canon_init_agent(state: StoryState) -> StoryState:
    """
    阶段2.2：Canon 初始化（第一版设定）
    - 在 planner 之后执行一次
    - 只在 canon 仍为占位/空时写入，避免覆盖人工维护内容
    - 有 LLM：生成 world/characters/timeline 的结构化初稿
    - 无 LLM：写入最小可用模板（保证后续流程能注入到 prompt）
    """
    logger = state.get("logger")
    chapter_index = int(state.get("chapter_index", 0) or 0)
    if logger:
        logger.event("node_start", node="canon_init", chapter_index=chapter_index)

    project_dir = str(state.get("project_dir", "") or "")
    if not project_dir:
        # 没有持久化项目目录就无法初始化
        if logger:
            logger.event("node_end", node="canon_init", chapter_index=chapter_index, skipped=True, reason="missing_project_dir")
        return state

    canon_dir = os.path.join(project_dir, "canon")
    world_path = os.path.join(canon_dir, "world.json")
    characters_path = os.path.join(canon_dir, "characters.json")
    timeline_path = os.path.join(canon_dir, "timeline.json")
    style_path = os.path.join(canon_dir, "style.md")

    existing_world = read_json(world_path) or {}
    existing_characters = read_json(characters_path) or {}
    existing_timeline = read_json(timeline_path) or {}
    existing_style = ""
    try:
        bundle = load_canon_bundle(project_dir)
        existing_style = str(bundle.get("style", "") or "")
    except Exception:
        existing_style = ""

    need_world = _is_placeholder_world(existing_world)
    need_chars = _is_placeholder_characters(existing_characters)
    need_timeline = _is_placeholder_timeline(existing_timeline)
    need_style = not existing_style.strip()

    # 都不需要则直接跳过
    if not (need_world or need_chars or need_timeline or need_style):
        if logger:
            logger.event("node_end", node="canon_init", chapter_index=chapter_index, skipped=True, reason="canon_already_filled")
        return state

    idea = str(state.get("user_input", "") or "")
    planner_result = state.get("planner_result") or {}

    llm = state.get("llm")
    if llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception:
            llm = None

    if llm:
        system = SystemMessage(
            content=(
                "你是小说项目的“设定初始化器”。你将基于用户一句话点子与策划任务书，产出第一版可执行设定（Canon）。\n"
                "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
                "输出 JSON schema：\n"
                "{\n"
                '  "world": {"rules":[{"name":"string","detail":"string"}], "factions":[{"name":"string","detail":"string"}], "places":[{"name":"string","detail":"string"}], "notes":"string"},\n'
                '  "characters": {"characters":[{"name":"string","role":"string","personality":"string","motivation":"string","abilities":"string","taboos":"string","relationships":[{"with":"string","relation":"string"}]}]},\n'
                '  "timeline": {"events":[{"order":1,"when":"string","what":"string","impact":"string"}]},\n'
                '  "style_suggestions": "string"\n'
                "}\n"
                "要求：\n"
                "- world/characters/timeline 必须可落盘直接用；避免空数组。\n"
                "- 不要过度发散：控制在 6-12 条规则/事件量级。\n"
                "- 设定要服务写作：包含可用于制造冲突的规则与人物动机。\n"
            )
        )
        human = HumanMessage(
            content=(
                f"用户点子：{idea}\n\n"
                f"策划任务书（planner_result）：{planner_result}\n\n"
                "注意：这是第一版设定，后续会由架构师/角色导演持续维护。请给出稳健、可扩展的基础设定。"
            )
        )
        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="canon_init",
                chapter_index=chapter_index,
                messages=[system, human],
                model=model,
                base_url=str(getattr(llm, "base_url", "") or ""),
            ):
                resp = llm.invoke([system, human])
        else:
            resp = llm.invoke([system, human])
        text = (getattr(resp, "content", "") or "").strip()
        if logger:
            logger.event(
                "llm_response",
                node="canon_init",
                chapter_index=chapter_index,
                content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
            )
        try:
            obj = json.loads(text)
        except Exception:
            obj = {}

        if isinstance(obj, dict):
            new_world = obj.get("world") if isinstance(obj.get("world"), dict) else {}
            new_chars = obj.get("characters") if isinstance(obj.get("characters"), dict) else {}
            new_timeline = obj.get("timeline") if isinstance(obj.get("timeline"), dict) else {}
            style_suggestions = str(obj.get("style_suggestions", "") or "")
        else:
            new_world, new_chars, new_timeline, style_suggestions = {}, {}, {}, ""

        if need_world and isinstance(new_world, dict) and new_world:
            write_json(world_path, _merge_keep_existing(existing_world, new_world))
        if need_chars and isinstance(new_chars, dict) and new_chars:
            write_json(characters_path, _merge_keep_existing(existing_characters, new_chars))
        if need_timeline and isinstance(new_timeline, dict) and new_timeline:
            write_json(timeline_path, _merge_keep_existing(existing_timeline, new_timeline))
        if need_style and style_suggestions.strip():
            # 不覆盖用户已经写的 style.md；仅在空时写入建议
            write_text(style_path, style_suggestions.strip() + "\n")

        if logger:
            logger.event(
                "node_end",
                node="canon_init",
                chapter_index=chapter_index,
                used_llm=True,
                wrote_world=bool(need_world),
                wrote_characters=bool(need_chars),
                wrote_timeline=bool(need_timeline),
                wrote_style=bool(need_style and style_suggestions.strip()),
            )
        state["canon_init_used_llm"] = True
        return state

    # 模板兜底：写入最小可用结构（不追求质量，只保证后续可注入/可维护）
    if need_world:
        write_json(
            world_path,
            {
                "rules": [{"name": "修行体系", "detail": "世界存在修行体系，但细节待补充；不同宗门/势力有不同法门。"}],
                "factions": [{"name": "宗门A", "detail": "本地强势宗门，内部派系斗争激烈。"}],
                "places": [{"name": "山门", "detail": "故事开篇发生地，规矩森严。"}],
                "notes": "（模板）后续由架构师完善世界观规则/禁忌/体系。",
            },
        )
    if need_chars:
        write_json(
            characters_path,
            {
                "characters": [
                    {
                        "name": "主角",
                        "role": "外来者/新入门者",
                        "personality": "谨慎、好奇、有底线",
                        "motivation": "求生与自证",
                        "abilities": "未知（待觉醒）",
                        "taboos": "不要轻易暴露秘密",
                        "relationships": [],
                    }
                ]
            },
        )
    if need_timeline:
        write_json(
            timeline_path,
            {
                "events": [
                    {"order": 1, "when": "开篇", "what": "主角误入修仙世界并被宗门注意", "impact": "被迫卷入宗门纷争"}
                ]
            },
        )
    if need_style:
        write_text(style_path, "偏网文节奏：冲突前置，短句，多画面感，少空泛总结。\n")

    state["canon_init_used_llm"] = False
    if logger:
        logger.event(
            "node_end",
            node="canon_init",
            chapter_index=chapter_index,
            used_llm=False,
            wrote_world=bool(need_world),
            wrote_characters=bool(need_chars),
            wrote_timeline=bool(need_timeline),
            wrote_style=bool(need_style),
        )
    return state


