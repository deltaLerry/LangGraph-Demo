from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from state import StoryState
from debug_log import truncate_text
from llm_meta import extract_finish_reason_and_usage
from storage import load_canon_bundle
from json_utils import extract_first_json_object
from llm_call import invoke_with_retry
from llm_json import invoke_json_with_repair


def _extract_first_json_obj(text: str) -> Dict[str, Any]:
    return extract_first_json_object(text)


def memory_agent(state: StoryState) -> StoryState:
    """
    章节记忆 Agent（chapter memory）：
    - 只要本章产出落盘（writer_result 已有），就生成 chapter memory
    - 即使审核不通过 / 达到返工上限，也会生成（便于后续续写连续性与调试）
    - 有 LLM：抽取结构化 memory.json（摘要/事件/人物状态/新增事实/伏笔）
    - 无 LLM：给一个可用的模板结构（保证落盘/后续可检索）
    """
    logger = state.get("logger")
    chapter_index = int(state.get("chapter_index", 1))
    if logger:
        logger.event("node_start", node="memory", chapter_index=chapter_index)

    decision = str(state.get("editor_decision", "") or "").strip()
    # Human-in-the-loop：以总编验收为准（若未提供则回退到 editor_decision）
    human_approved = state.get("human_approved", None)
    if human_approved is None:
        approved = decision == "审核通过"
    else:
        approved = bool(human_approved)

    planner_result = state.get("planner_result") or {}
    writer_result = str(state.get("writer_result", "") or "")
    project_name = ""
    try:
        project_name = str((planner_result or {}).get("项目名称", "") or "")
    except Exception:
        project_name = ""

    llm = state.get("llm")
    if llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception:
            llm = None

    if llm:
        system = SystemMessage(
            content=(
                "你是小说项目的“记忆整理员”。你将把本章正文整理成可检索的结构化记忆。\n"
                "你必须且仅输出一个格式严格的 JSON 对象（不要解释、不要 markdown）。\n"
                "JSON schema（字段可为空，但必须是合法 JSON）：\n"
                "{\n"
                '  "chapter_index": number,\n'
                '  "summary": "string",\n'
                '  "events": [{"what":"string","where":"string","who":["string"],"result":"string"}],\n'
                '  "character_updates": [{"name":"string","status":"string","new_info":"string"}],\n'
                '  "new_facts": [{"type":"world|character|timeline|item|place|faction","key":"string","value":"string"}],\n'
                '  "open_threads": ["string"],\n'
                '  "style_notes": ["string"]\n'
                "}\n"
                "要求：summary 100~250字；events 3~8条；new_facts 只写本章明确新增/确认的信息。"
            )
        )
        human = HumanMessage(
            content=(
                f"项目：{project_name}\n"
                f"章节：第{chapter_index}章\n\n"
                "正文：\n"
                f"{writer_result}\n"
            )
        )

        schema_text = (
            "{\n"
            '  "chapter_index": number,\n'
            '  "summary": "string",\n'
            '  "events": [{"what":"string","where":"string","who":["string"],"result":"string"}],\n'
            '  "character_updates": [{"name":"string","status":"string","new_info":"string"}],\n'
            '  "new_facts": [{"type":"world|character|timeline|item|place|faction","key":"string","value":"string"}],\n'
            '  "open_threads": ["string"],\n'
            '  "style_notes": ["string"]\n'
            "}\n"
        )

        def _validate(m: Dict[str, Any]) -> str:
            # 轻量校验：至少要有 summary 与 events（否则后续不可用）
            if not str(m.get("summary", "") or "").strip():
                return "missing_or_empty_summary"
            ev = m.get("events")
            if not isinstance(ev, list) or len(ev) == 0:
                return "missing_or_empty_events"
            return ""

        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="memory",
                chapter_index=chapter_index,
                messages=[system, human],
                model=model,
                base_url=str(getattr(llm, "base_url", "") or ""),
            ):
                mem, _raw, _fr, _usage = invoke_json_with_repair(
                    llm=llm,
                    messages=[system, human],
                    schema_text=schema_text,
                    node="memory",
                    chapter_index=chapter_index,
                    logger=logger,
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                    validate=_validate,
                )
        else:
            mem, _raw, _fr, _usage = invoke_json_with_repair(
                llm=llm,
                messages=[system, human],
                schema_text=schema_text,
                node="memory",
                chapter_index=chapter_index,
                logger=None,
                max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                validate=_validate,
            )

        mem["chapter_index"] = chapter_index
        # 额外元信息（非 LLM 生成字段）：用于后续区分“通过/不通过”的记忆来源
        mem["editor_decision"] = decision
        mem["human_decision"] = str(state.get("human_decision", "") or "")
        mem["approved"] = approved
        state["chapter_memory"] = mem
        state["memory_used_llm"] = True
        if logger:
            logger.event(
                "node_end",
                node="memory",
                chapter_index=chapter_index,
                used_llm=True,
                summary_chars=len(str(mem.get("summary", "") or "")),
            )
        return state

    # 模板兜底
    # 模板模式下不要用正则“猜人名”（很容易把项目名切碎污染设定）；
    # 优先取 Canon 的第一个角色名作为主角锚点，保证后续 canon_update 不产生垃圾角色。
    people: List[str] = []
    project_dir = str(state.get("project_dir", "") or "")
    try:
        if project_dir:
            canon = load_canon_bundle(project_dir)
            chars = canon.get("characters") if isinstance(canon.get("characters"), dict) else {}
            arr = chars.get("characters") if isinstance(chars.get("characters"), list) else []
            if arr and isinstance(arr[0], dict):
                n = str(arr[0].get("name", "") or "").strip()
                if n:
                    people = [n]
    except Exception:
        people = []
    if not people:
        people = ["主角"]
    mem = {
        "chapter_index": chapter_index,
        "editor_decision": decision,
        "human_decision": str(state.get("human_decision", "") or ""),
        "approved": approved,
        "summary": (writer_result[:180] + "…") if len(writer_result) > 180 else writer_result,
        "events": [
            {
                "what": "（模板）本章发生的关键事件待补充",
                "where": "",
                "who": people,
                "result": "",
            }
        ],
        "character_updates": [{"name": p, "status": "", "new_info": ""} for p in people[:1]],
        "new_facts": [],
        "open_threads": [],
        "style_notes": [],
    }
    state["chapter_memory"] = mem
    state["memory_used_llm"] = False
    if logger:
        logger.event("node_end", node="memory", chapter_index=chapter_index, used_llm=False)
    return state


