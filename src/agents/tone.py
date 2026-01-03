from __future__ import annotations

import json
from typing import Any, Dict

from state import StoryState
from debug_log import truncate_text
from json_utils import extract_first_json_object
from llm_meta import extract_finish_reason_and_usage
from storage import load_canon_bundle


def _extract(text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(text)
    return obj if isinstance(obj, dict) else {}


def tone_agent(state: StoryState) -> StoryState:
    """
    阶段3：基调/文风策划（开篇基调）
    输出严格 JSON：用于 materials_bundle.tone（并可用于后续生成 style.md 的建议）。
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="tone", chapter_index=0)

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
                if str(t.get("任务名称", "") or "").strip() == "开篇基调":
                    instr = str(t.get("任务指令", "") or "").strip()
                    break
    except Exception:
        instr = ""

    project_dir = str(state.get("project_dir", "") or "")
    canon = load_canon_bundle(project_dir) if project_dir else {"world": {}, "characters": {}, "timeline": {}, "style": ""}
    style_text = truncate_text(str(canon.get("style", "") or ""), max_chars=2200)

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
                "你是小说项目的“基调策划”，负责把文风约束写成可执行清单。\n"
                "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余文字）。\n"
                "输出 JSON schema（字段允许为空，但必须是合法 JSON）：\n"
                "{\n"
                '  "narration": "string",\n'
                '  "pacing": "string",\n'
                '  "reference_style": "string",\n'
                '  "style_constraints": ["string"],\n'
                '  "avoid": ["string"]\n'
                "}\n"
                "要求：\n"
                "- style_constraints 8~15条，必须是“写作可执行规则”，不要空泛。\n"
                "- avoid 5~10条，专门列出‘AI味/套话/常见失误’。\n"
                "- 若 Canon 的 style.md 已有约束，请先继承并补全，不要互相冲突。\n"
            )
        )
        human = HumanMessage(
            content=(
                f"项目：{project_name}\n"
                f"点子：{idea}\n"
                + (f"\n策划任务书（开篇基调）：\n{instr}\n" if instr else "")
                + "\n【Canon style.md（真值来源，若存在需遵守/补全）】\n"
                + f"{style_text}\n"
            )
        )
        resp = _invoke_once("tone", system, human)
        text = (getattr(resp, "content", "") or "").strip()
        if logger:
            fr, usage = extract_finish_reason_and_usage(resp)
            logger.event(
                "llm_response",
                node="tone",
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
                    "你是小说项目的“基调策划”。你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
                    "务必短：确保 JSON 完整可解析。\n"
                    "硬性约束：\n"
                    "- style_constraints 6~10条，每条<=30字\n"
                    "- avoid 5~8条，每条<=20字\n"
                    "- narration/pacing/reference_style 尽量短\n"
                    "输出 JSON schema 与上一次相同。\n"
                )
            )
            resp2 = _invoke_once("tone_retry", system_retry, human)
            text2 = (getattr(resp2, "content", "") or "").strip()
            if logger:
                fr2, usage2 = extract_finish_reason_and_usage(resp2)
                logger.event(
                    "llm_response",
                    node="tone_retry",
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
                raise ValueError("tone_agent: 无法从 LLM 输出中提取 JSON（已重试）")
            if logger:
                logger.event("llm_parse_failed", node="tone", chapter_index=0, action="fallback_template")
            llm = None
        else:
            state["tone_result"] = obj
            state["tone_used_llm"] = True
            if logger:
                logger.event("node_end", node="tone", chapter_index=0, used_llm=True)
            return state

    state["tone_result"] = {
        "narration": "（模板）第三人称/或第一人称（后续可明确）",
        "pacing": "（模板）开篇节奏偏快，冲突前置，信息通过行动与对话自然露出。",
        "reference_style": "",
        "style_constraints": ["避免总结句", "句式多样，减少机械重复", "设定不讲解，靠场景呈现"],
        "avoid": ["AI味总结", "大段百科说明", "重复句式堆砌"],
    }
    state["tone_used_llm"] = False
    if logger:
        logger.event("node_end", node="tone", chapter_index=0, used_llm=False)
    return state


