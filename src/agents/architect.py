from __future__ import annotations

import json
from typing import Any, Dict

from state import StoryState
from debug_log import truncate_text
from json_utils import extract_first_json_object
from llm_meta import extract_finish_reason_and_usage


def _extract(text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(text)
    return obj if isinstance(obj, dict) else {}


def architect_agent(state: StoryState) -> StoryState:
    """
    阶段3：架构师（世界观）
    输出严格 JSON：用于后续 materials_bundle.world（尽量兼容 canon/world.json 的结构）。
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="architect", chapter_index=0)

    idea = str(state.get("user_input", "") or "")
    planner_result = state.get("planner_result") or {}
    project_name = ""
    try:
        project_name = str(planner_result.get("项目名称", "") or "")
    except Exception:
        project_name = ""

    instr = ""
    try:
        tasks = planner_result.get("任务列表") if isinstance(planner_result, dict) else []
        if isinstance(tasks, list):
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                if str(t.get("任务名称", "") or "").strip() == "世界观设定":
                    instr = str(t.get("任务指令", "") or "").strip()
                    break
    except Exception:
        instr = ""

    llm = state.get("llm")
    if llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception as e:  # pragma: no cover
            if state.get("force_llm", False):
                raise RuntimeError("已指定 LLM 模式，但无法导入 langchain_core.messages") from e
            llm = None

    if llm:
        system = SystemMessage(
            content=(
                "你是小说项目的“架构师”，负责构建世界观设定。\n"
                "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余文字）。\n"
                "输出 JSON schema（字段允许为空，但必须是合法 JSON）：\n"
                "{\n"
                '  "rules": [{"name":"string","desc":"string"}],\n'
                '  "factions": [{"name":"string","desc":"string"}],\n'
                '  "places": [{"name":"string","desc":"string"}],\n'
                '  "notes": "string"\n'
                "}\n"
                "要求：\n"
                "- rules/factions/places 每类 3~8 条，尽量具体可用于写作约束。\n"
                "- notes 用于补充“世界规则/禁忌/核心冲突”的一句话摘要。\n"
            )
        )
        human = HumanMessage(
            content=(
                f"项目：{project_name}\n"
                f"点子：{idea}\n"
                + (f"\n策划任务书（世界观设定）：\n{instr}\n" if instr else "")
            )
        )
        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(node="architect", chapter_index=0, messages=[system, human], model=model, base_url=str(getattr(llm, "base_url", "") or "")):
                resp = llm.invoke([system, human])
        else:
            resp = llm.invoke([system, human])
        text = (getattr(resp, "content", "") or "").strip()
        if logger:
            fr, usage = extract_finish_reason_and_usage(resp)
            logger.event(
                "llm_response",
                node="architect",
                chapter_index=0,
                content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
                finish_reason=fr,
                token_usage=usage,
            )
        obj = _extract(text)
        if not obj:
            raise ValueError("architect_agent: 无法从 LLM 输出中提取 JSON")
        state["architect_result"] = obj
        state["architect_used_llm"] = True
        if logger:
            logger.event("node_end", node="architect", chapter_index=0, used_llm=True)
        return state

    # 模板兜底：保证结构存在，方便后续汇总/落盘
    state["architect_result"] = {
        "rules": [
            {"name": "（模板）世界规则", "desc": "以点子为核心设定的通用规则，后续可由 LLM 补全。"},
        ],
        "factions": [
            {"name": "（模板）主要势力", "desc": "围绕核心冲突的势力描述，后续可补充细节。"},
        ],
        "places": [
            {"name": "（模板）关键地点", "desc": "故事开篇/冲突发生的主要地点。"},
        ],
        "notes": f"（模板）世界观摘要：{idea[:80]}",
    }
    state["architect_used_llm"] = False
    if logger:
        logger.event("node_end", node="architect", chapter_index=0, used_llm=False)
    return state


