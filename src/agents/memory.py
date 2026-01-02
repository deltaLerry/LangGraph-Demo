from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from state import StoryState
from debug_log import truncate_text
from llm_meta import extract_finish_reason_and_usage
from storage import load_canon_bundle


def _extract_first_json_obj(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def memory_agent(state: StoryState) -> StoryState:
    """
    章节记忆 Agent（chapter memory）：
    - 仅在 editor 审核通过后触发
    - 有 LLM：抽取结构化 memory.json（摘要/事件/人物状态/新增事实/伏笔）
    - 无 LLM：给一个可用的模板结构（保证落盘/后续可检索）
    """
    logger = state.get("logger")
    chapter_index = int(state.get("chapter_index", 1))
    if logger:
        logger.event("node_start", node="memory", chapter_index=chapter_index)

    decision = str(state.get("editor_decision", "") or "")
    if decision != "审核通过":
        # 安全：不通过不生成
        state["chapter_memory"] = {}
        state["memory_used_llm"] = False
        if logger:
            logger.event("node_end", node="memory", chapter_index=chapter_index, used_llm=False, skipped=True)
        return state

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
        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="memory",
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
            finish_reason, token_usage = extract_finish_reason_and_usage(resp)
            logger.event(
                "llm_response",
                node="memory",
                chapter_index=chapter_index,
                content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
                finish_reason=finish_reason,
                token_usage=token_usage,
            )
        mem = _extract_first_json_obj(text)
        mem["chapter_index"] = chapter_index
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


