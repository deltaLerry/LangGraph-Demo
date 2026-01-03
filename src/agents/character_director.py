from __future__ import annotations

import json
from typing import Any, Dict, List

from state import StoryState
from debug_log import truncate_text
from json_utils import extract_first_json_object
from llm_meta import extract_finish_reason_and_usage
from storage import load_canon_bundle


def _extract(text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(text)
    return obj if isinstance(obj, dict) else {}


def character_director_agent(state: StoryState) -> StoryState:
    """
    阶段3：角色导演（人物卡）
    输出严格 JSON：用于 materials_bundle.characters。
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="character_director", chapter_index=0)

    idea = str(state.get("user_input", "") or "")
    planner_result = state.get("planner_result") or {}
    project_name = str((planner_result or {}).get("项目名称", "") or "")

    instr = ""
    try:
        tasks = planner_result.get("任务列表") if isinstance(planner_result, dict) else []
        if isinstance(tasks, list):
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                if str(t.get("任务名称", "") or "").strip() == "核心角色":
                    instr = str(t.get("任务指令", "") or "").strip()
                    break
    except Exception:
        instr = ""

    project_dir = str(state.get("project_dir", "") or "")
    canon = load_canon_bundle(project_dir) if project_dir else {"world": {}, "characters": {}, "timeline": {}, "style": ""}
    canon_chars = canon.get("characters") if isinstance(canon.get("characters"), dict) else {}
    canon_chars_text = truncate_text(json.dumps(canon_chars, ensure_ascii=False, indent=2), max_chars=3500)

    llm = state.get("llm")
    if llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception as e:  # pragma: no cover
            if state.get("force_llm", False):
                raise RuntimeError("已指定 LLM 模式，但无法导入 langchain_core.messages") from e
            llm = None

    if llm:
        def _invoke_once(node_name: str, system_msg: SystemMessage, human_msg: HumanMessage):
            if logger:
                model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
                with logger.llm_call(
                    node=node_name,
                    chapter_index=0,
                    messages=[system_msg, human_msg],
                    model=model,
                    base_url=str(getattr(llm, "base_url", "") or ""),
                ):
                    return llm.invoke([system_msg, human_msg])
            return llm.invoke([system_msg, human_msg])

        system = SystemMessage(
            content=(
                "你是小说项目的“角色导演”，负责产出可执行的人物卡。\n"
                "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余文字）。\n"
                "输出 JSON schema（字段允许为空，但必须是合法 JSON）：\n"
                "{\n"
                '  "characters": [\n'
                "    {\n"
                '      "name": "string",\n'
                '      "traits": ["string"],\n'
                '      "motivation": "string",\n'
                '      "background": "string",\n'
                '      "abilities": ["string"],\n'
                '      "taboos": ["string"],\n'
                '      "relationships": ["string"],\n'
                '      "notes": "string"\n'
                "    }\n"
                "  ]\n"
                "}\n"
                "要求：\n"
                "- 产出 3~6 个主要人物；每个角色必须有明确动机 + 1~3 个禁忌（便于写作一致性约束）。\n"
                "- 若 Canon 已存在角色（同名），请只做“补全/增量”，不要改名、不要推翻既有条目。\n"
            )
        )
        human = HumanMessage(
            content=(
                f"项目：{project_name}\n"
                f"点子：{idea}\n"
                + (f"\n策划任务书（核心角色）：\n{instr}\n" if instr else "")
                + "\n【Canon人物卡（真值来源，若存在需遵守/补全）】\n"
                + f"{canon_chars_text}\n"
            )
        )
        resp = _invoke_once("character_director", system, human)
        text = (getattr(resp, "content", "") or "").strip()
        if logger:
            fr, usage = extract_finish_reason_and_usage(resp)
            logger.event(
                "llm_response",
                node="character_director",
                chapter_index=0,
                content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
                finish_reason=fr,
                token_usage=usage,
            )
        obj = _extract(text)
        fr0, _usage0 = extract_finish_reason_and_usage(resp)

        if (not obj) or (fr0 and str(fr0).lower() == "length"):
            system_retry = SystemMessage(
                content=(
                    "你是小说项目的“角色导演”。你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
                    "务必短：确保 JSON 完整可解析。\n"
                    "硬性约束：\n"
                    "- characters 3个以内\n"
                    "- traits/abilities/taboos 每项 1~3 条\n"
                    "- motivation/background/notes 每项<=80字\n"
                    "输出 JSON schema 与上一次相同。\n"
                )
            )
            resp2 = _invoke_once("character_director_retry", system_retry, human)
            text2 = (getattr(resp2, "content", "") or "").strip()
            if logger:
                fr2, usage2 = extract_finish_reason_and_usage(resp2)
                logger.event(
                    "llm_response",
                    node="character_director_retry",
                    chapter_index=0,
                    content=truncate_text(text2, max_chars=getattr(logger, "max_chars", 20000)),
                    finish_reason=fr2,
                    token_usage=usage2,
                )
            obj2 = _extract(text2)
            if obj2:
                obj = obj2

        if not obj:
            if state.get("force_llm", False):
                raise ValueError("character_director_agent: 无法从 LLM 输出中提取 JSON（已重试）")
            if logger:
                logger.event("llm_parse_failed", node="character_director", chapter_index=0, action="fallback_template")
            llm = None
        else:
            state["character_director_result"] = obj
            state["character_director_used_llm"] = True
            if logger:
                logger.event("node_end", node="character_director", chapter_index=0, used_llm=True)
            return state

    # 模板兜底
    state["character_director_result"] = {
        "characters": [
            {
                "name": "主角",
                "traits": ["谨慎", "自尊强"],
                "motivation": "想弄清自己被卷入事件的真相，并保住自己与重要之人的安全。",
                "background": "（模板）出身平凡/或有隐秘来历，后续可补全。",
                "abilities": [],
                "taboos": ["不轻易欠人情", "不在公开场合示弱"],
                "relationships": [],
                "notes": "（模板）可作为第一视角/叙事锚点。",
            }
        ]
    }
    state["character_director_used_llm"] = False
    if logger:
        logger.event("node_end", node="character_director", chapter_index=0, used_llm=False)
    return state


