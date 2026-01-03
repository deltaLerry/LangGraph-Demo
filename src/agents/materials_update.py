from __future__ import annotations

import json
from typing import Any, Dict, List

from state import StoryState
from debug_log import truncate_text
from llm_meta import extract_finish_reason_and_usage
from json_utils import extract_first_json_object
from storage import load_canon_bundle
from materials import materials_prompt_digest


def _extract(text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(text)
    return obj if isinstance(obj, dict) else {}


def materials_update_agent(state: StoryState) -> StoryState:
    """
    阶段3：材料复盘会议（materials_update）
    - 输入：本章 chapter_memory + editor_report + 当前 materials_bundle + Canon
    - 输出：materials_update_suggestions（默认不落地写入；走预览→确认→应用）

    核心约束（你提出的点）：materials 的讨论/更新必须受 Canon 的硬约束。
    因此：
    - 若需要改变“事实/设定”，必须输出 canon_patch（交给现有 apply_canon_suggestions 流程）
    - 只允许对“计划类材料”（outline/tone 等）输出 materials_patch
    """
    logger = state.get("logger")
    chapter_index = int(state.get("chapter_index", 1))
    if logger:
        logger.event("node_start", node="materials_update", chapter_index=chapter_index)

    project_dir = str(state.get("project_dir", "") or "")
    canon = load_canon_bundle(project_dir) if project_dir else {"world": {}, "characters": {}, "timeline": {}, "style": ""}
    canon_text = truncate_text(
        json.dumps(
            {
                "world": canon.get("world", {}) or {},
                "characters": canon.get("characters", {}) or {},
                "timeline": canon.get("timeline", {}) or {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        max_chars=5500,
    )
    style_text = truncate_text(str(canon.get("style", "") or ""), max_chars=1600)

    mem = state.get("chapter_memory") if isinstance(state.get("chapter_memory"), dict) else {}
    editor_report = state.get("editor_report") if isinstance(state.get("editor_report"), dict) else {}
    materials_bundle = state.get("materials_bundle") if isinstance(state.get("materials_bundle"), dict) else {}

    # 没有材料包就不做复盘（避免空建议）
    if not materials_bundle:
        state["materials_update_used"] = False
        state["materials_update_suggestions"] = []
        if logger:
            logger.event("node_end", node="materials_update", chapter_index=chapter_index, skipped=True, reason="missing_materials_bundle")
        return state

    llm = state.get("llm")
    if llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception:
            llm = None

    # template：先给空建议（后续可在无LLM时做规则化建议）
    if not llm:
        state["materials_update_used"] = False
        state["materials_update_suggestions"] = []
        if logger:
            logger.event("node_end", node="materials_update", chapter_index=chapter_index, used_llm=False, suggestions_count=0)
        return state

    # LLM：输出严格 JSON，且强制 Canon 硬约束
    system = SystemMessage(
        content=(
            "你是小说项目的“复盘会议主持人”。你的任务是根据本章产出与主编报告，更新‘计划类材料’以提升后续写作一致性。\n"
            "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余文字）。\n"
            "硬性原则（必须遵守）：\n"
            "1) Canon 是硬约束/真值来源：materials 任何更新都不得与 Canon 冲突。\n"
            "2) 如果你发现 Canon 本身缺条目或需要补全/修正设定，请不要直接改 materials；请输出 action=canon_patch 的建议。\n"
            "3) materials_patch 只允许修改 ‘计划类材料’：outline.json / tone.json。\n"
            "输出 JSON schema：\n"
            "{\n"
            '  "items": [\n'
            "    {\n"
            '      "action": "materials_patch|canon_patch",\n'
            '      "issue": "string",\n'
            '      "quote": "string",\n'
            '      "type": "outline|tone|world|character|timeline|style",\n'
            '      "canon_patch": {"target":"world.json|characters.json|timeline.json|style.md|N/A","op":"append|note|N/A","path":"string|N/A","value":"any|N/A"},\n'
            '      "materials_patch": {"target":"outline.json|tone.json|N/A","op":"append|note|N/A","path":"string|N/A","value":"any|N/A"}\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "补丁约束（为了可安全应用）：\n"
            "- outline.json：优先 op=append path=chapters，value 为章节 dict 或 list（必须包含 chapter_index）。允许 op=note 写 notes。\n"
            "- tone.json：op=append path=style_constraints 或 avoid，value 为 string 或 list；允许 op=note 写 notes。\n"
            "输出要求：items 宁可少而准；无明确依据就不要输出。"
        )
    )

    digest = materials_prompt_digest(materials_bundle, chapter_index=chapter_index)
    human = HumanMessage(
        content=(
            f"当前章节：第{chapter_index}章\n\n"
            "【Canon（硬约束/真值来源）】\n"
            f"{canon_text}\n\n"
            "【Canon style.md（硬约束）】\n"
            f"{style_text}\n\n"
            "【当前材料包（计划类材料，需受 Canon 约束）】\n"
            f"{digest}\n\n"
            "【本章 chapter_memory】\n"
            f"{truncate_text(json.dumps(mem, ensure_ascii=False, indent=2), max_chars=2500)}\n\n"
            "【本章 editor_report】\n"
            f"{truncate_text(json.dumps(editor_report, ensure_ascii=False, indent=2), max_chars=2500)}\n"
        )
    )

    if logger:
        model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
        with logger.llm_call(
            node="materials_update",
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
        fr, usage = extract_finish_reason_and_usage(resp)
        logger.event(
            "llm_response",
            node="materials_update",
            chapter_index=chapter_index,
            content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
            finish_reason=fr,
            token_usage=usage,
        )

    obj = _extract(text)
    items = obj.get("items") if isinstance(obj.get("items"), list) else []
    out: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)

    state["materials_update_suggestions"] = out
    state["materials_update_used"] = bool(out)
    if logger:
        logger.event(
            "node_end",
            node="materials_update",
            chapter_index=chapter_index,
            used_llm=True,
            suggestions_count=len(out),
        )
    return state


