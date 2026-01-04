from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

from state import StoryState
from debug_log import truncate_text
import os

from storage import load_canon_bundle, read_json, write_json, write_text
from llm_meta import extract_finish_reason_and_usage
from json_utils import extract_first_json_object
from json_utils import extract_first_json_object_with_error
from llm_call import invoke_with_retry
from llm_json import repair_json_only


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


def _extract_first_json_obj(text: str) -> Dict[str, Any]:
    return extract_first_json_object(text)


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
    # style.md 不在此处兜底生成：必须来自用户输入（由主流程负责创建/校验）
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
    # 注意：style.md 必须来自用户输入；canon_init 不负责生成/覆盖 style
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
        def _invoke_once(node_name: str, system_msg: SystemMessage, human_msg: HumanMessage) -> tuple[str, str | None]:
            if logger:
                model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
                with logger.llm_call(
                    node=node_name,
                    chapter_index=chapter_index,
                    messages=[system_msg, human_msg],
                    model=model,
                    base_url=str(getattr(llm, "base_url", "") or ""),
                ):
                    resp0 = invoke_with_retry(
                        llm,
                        [system_msg, human_msg],
                        max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                        base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                        logger=logger,
                        node=node_name,
                        chapter_index=chapter_index,
                    )
            else:
                resp0 = invoke_with_retry(
                    llm,
                    [system_msg, human_msg],
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                )
            text0 = (getattr(resp0, "content", "") or "").strip()
            fr0, usage0 = extract_finish_reason_and_usage(resp0)
            if logger:
                logger.event(
                    "llm_response",
                    node=node_name,
                    chapter_index=chapter_index,
                    content=truncate_text(text0, max_chars=getattr(logger, "max_chars", 20000)),
                    finish_reason=fr0,
                    token_usage=usage0,
                )
            return text0, fr0

        # 第一次尝试
        text, fr = _invoke_once("canon_init", system, human)
        obj, err = extract_first_json_object_with_error(text)

        # 如果不是 length，而是明确解析错误：立刻把错误原因回传给 LLM 做 JSON 修复（更“解决问题”）
        if (not obj) and (not (fr and str(fr).lower() == "length")):
            try:
                schema_text = (
                    "{\n"
                    '  "world": {"rules":[{"name":"string","detail":"string"}], "factions":[{"name":"string","detail":"string"}], "places":[{"name":"string","detail":"string"}], "notes":"string"},\n'
                    '  "characters": {"characters":[{"name":"string","role":"string","personality":"string","motivation":"string","abilities":"string","taboos":"string","relationships":[{"with":"string","relation":"string"}]}]},\n'
                    '  "timeline": {"events":[{"order":1,"when":"string","what":"string","impact":"string"}]},\n'
                    '  "style_suggestions": "string"\n'
                    "}\n"
                )
                obj_fix = repair_json_only(
                    llm=llm,
                    bad_text=text,
                    err=err or "unknown_parse_error",
                    schema_text=schema_text,
                    node="canon_init_fix_json",
                    chapter_index=chapter_index,
                    logger=logger,
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                )
                if obj_fix:
                    obj, err = obj_fix, ""
            except Exception:
                pass

        # 如果被截断 / 解析失败：做一次更短、更保守的重试，避免 writer/editor 拿到空 Canon 导致通过率极低
        if (not obj) or (fr and str(fr).lower() == "length"):
            system_retry = SystemMessage(
                content=(
                    "你是小说项目的“设定初始化器”。你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
                    "务必短：控制总条目数与字段长度，确保 JSON 完整可解析。\n"
                    "硬性约束：\n"
                    "- world.rules 6条以内\n"
                    "- world.factions 3条以内\n"
                    "- world.places 3条以内\n"
                    "- characters.characters 3个角色以内\n"
                    "- timeline.events 6条以内\n"
                    "- 每个 detail 不超过 120 字\n"
                    "- style_suggestions 不超过 180 字\n"
                    "输出 JSON schema 与上一次相同。\n"
                )
            )
            text2, _fr2 = _invoke_once("canon_init_retry", system_retry, human)
            obj2, err2 = extract_first_json_object_with_error(text2)
            if obj2:
                obj, err = obj2, ""
            else:
                # 再把解析错误回传给 LLM，做一次针对性 JSON 修复
                try:
                    schema_text = (
                        "{\n"
                        '  "world": {"rules":[{"name":"string","detail":"string"}], "factions":[{"name":"string","detail":"string"}], "places":[{"name":"string","detail":"string"}], "notes":"string"},\n'
                        '  "characters": {"characters":[{"name":"string","role":"string","personality":"string","motivation":"string","abilities":"string","taboos":"string","relationships":[{"with":"string","relation":"string"}]}]},\n'
                        '  "timeline": {"events":[{"order":1,"when":"string","what":"string","impact":"string"}]},\n'
                        '  "style_suggestions": "string"\n'
                        "}\n"
                    )
                    obj_fix2 = repair_json_only(
                        llm=llm,
                        bad_text=text2,
                        err=err2 or err or "unknown_parse_error",
                        schema_text=schema_text,
                        node="canon_init_retry_fix_json",
                        chapter_index=chapter_index,
                        logger=logger,
                        max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                        base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                    )
                    if obj_fix2:
                        obj = obj_fix2
                except Exception:
                    pass
        new_world = obj.get("world") if isinstance(obj.get("world"), dict) else {}
        new_chars = obj.get("characters") if isinstance(obj.get("characters"), dict) else {}
        new_timeline = obj.get("timeline") if isinstance(obj.get("timeline"), dict) else {}
        # style_suggestions 仅用于提示/日志；不写入 style.md（style.md 必须来自用户输入）
        style_suggestions = str(obj.get("style_suggestions", "") or "")

        # 若两次仍失败：立刻写入“最小可用模板”，避免 writer/editor 拿到空 Canon 导致通过率极低
        if not (new_world or new_chars or new_timeline or style_suggestions.strip()):
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
                    {"events": [{"order": 1, "when": "开篇", "what": "主角误入修仙世界并被宗门注意", "impact": "被迫卷入宗门纷争"}]},
                )
            # style.md 不在此处兜底写入

            if logger:
                logger.event(
                    "node_end",
                    node="canon_init",
                    chapter_index=chapter_index,
                    used_llm=True,
                    wrote_world=bool(need_world),
                    wrote_characters=bool(need_chars),
                    wrote_timeline=bool(need_timeline),
                    wrote_style=False,
                    fallback_template=True,
                )
            state["canon_init_used_llm"] = True
            return state

        wrote_world = False
        wrote_characters = False
        wrote_timeline = False
        wrote_style = False

        if need_world and isinstance(new_world, dict) and new_world:
            write_json(world_path, _merge_keep_existing(existing_world, new_world))
            wrote_world = True
        if need_chars and isinstance(new_chars, dict) and new_chars:
            write_json(characters_path, _merge_keep_existing(existing_characters, new_chars))
            wrote_characters = True
        if need_timeline and isinstance(new_timeline, dict) and new_timeline:
            write_json(timeline_path, _merge_keep_existing(existing_timeline, new_timeline))
            wrote_timeline = True
        # style.md 不在此处写入（必须来自用户输入）

        if logger:
            logger.event(
                "node_end",
                node="canon_init",
                chapter_index=chapter_index,
                used_llm=True,
                wrote_world=wrote_world,
                wrote_characters=wrote_characters,
                wrote_timeline=wrote_timeline,
                wrote_style=wrote_style,
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
    # style.md 不在此处兜底写入

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
            wrote_style=False,
        )
    return state


