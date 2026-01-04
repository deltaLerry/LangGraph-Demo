from __future__ import annotations

import json
from typing import Any, Dict

from state import StoryState
from debug_log import truncate_text
from json_utils import extract_first_json_object
from llm_meta import extract_finish_reason_and_usage
from llm_call import invoke_with_retry


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
    chapters_total = int(state.get("chapters_total", 1) or 1)
    target_words = int(state.get("target_words", 800) or 800)
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
                    return invoke_with_retry(
                        llm,
                        [system_msg, human_msg],
                        max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                        base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                        logger=logger,
                        node=node_name,
                        chapter_index=0,
                    )
            return invoke_with_retry(
                llm,
                [system_msg, human_msg],
                max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
            )

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
                f"- 本次项目规模：总章数={chapters_total}；每章目标字数≈{target_words}（中文字符数近似）。请按规模控制设定密度：长篇需留出可扩展空间与多阶段冲突升级。\n"
            )
        )
        human = HumanMessage(
            content=(
                f"项目：{project_name}\n"
                f"点子：{idea}\n"
                f"章节数：{chapters_total}\n"
                f"每章目标字数：{target_words}\n"
                + (f"\n策划任务书（世界观设定）：\n{instr}\n" if instr else "")
            )
        )
        resp = _invoke_once("architect", system, human)
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
        fr0, _usage0 = extract_finish_reason_and_usage(resp)

        # 失败/截断：做一次短版重试（更稳）
        if (not obj) or (fr0 and str(fr0).lower() == "length"):
            system_retry = SystemMessage(
                content=(
                    "你是小说项目的“架构师”。你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
                    "务必短：确保 JSON 完整可解析。\n"
                    "硬性约束：\n"
                    "- rules 6条以内\n"
                    "- factions 3条以内\n"
                    "- places 3条以内\n"
                    "- 每条 desc 不超过 120 字\n"
                    "- notes 不超过 180 字\n"
                    "输出 JSON schema 与上一次相同。\n"
                )
            )
            resp2 = _invoke_once("architect_retry", system_retry, human)
            text2 = (getattr(resp2, "content", "") or "").strip()
            if logger:
                fr2, usage2 = extract_finish_reason_and_usage(resp2)
                logger.event(
                    "llm_response",
                    node="architect_retry",
                    chapter_index=0,
                    content=truncate_text(text2, max_chars=getattr(logger, "max_chars", 20000)),
                    finish_reason=fr2,
                    token_usage=usage2,
                )
            obj2 = _extract(text2)
            if obj2:
                obj = obj2

        # 若仍失败：auto 模式降级为模板；force_llm 则抛错
        if not obj:
            if state.get("force_llm", False):
                raise ValueError("architect_agent: 无法从 LLM 输出中提取 JSON（已重试）")
            if logger:
                logger.event("llm_parse_failed", node="architect", chapter_index=0, action="fallback_template")
            llm = None
        else:
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


