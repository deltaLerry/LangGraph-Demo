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


def screenwriter_agent(state: StoryState) -> StoryState:
    """
    阶段3：编剧（主线+章节细纲）
    输出严格 JSON：用于 materials_bundle.outline。
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="screenwriter", chapter_index=0)

    idea = str(state.get("user_input", "") or "")
    chapters_total = int(state.get("chapters_total", 1) or 1)
    planner_result = state.get("planner_result") or {}
    project_name = str((planner_result or {}).get("项目名称", "") or "")

    instr = ""
    try:
        tasks = planner_result.get("任务列表") if isinstance(planner_result, dict) else []
        if isinstance(tasks, list):
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                if str(t.get("任务名称", "") or "").strip() == "主线脉络":
                    instr = str(t.get("任务指令", "") or "").strip()
                    break
    except Exception:
        instr = ""

    project_dir = str(state.get("project_dir", "") or "")
    canon = load_canon_bundle(project_dir) if project_dir else {"world": {}, "characters": {}, "timeline": {}, "style": ""}
    canon_world = canon.get("world") if isinstance(canon.get("world"), dict) else {}
    canon_chars = canon.get("characters") if isinstance(canon.get("characters"), dict) else {}
    canon_text = truncate_text(
        json.dumps({"world": canon_world, "characters": canon_chars}, ensure_ascii=False, indent=2),
        max_chars=4500,
    )

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
                "你是小说项目的“编剧”，负责主线与章节细纲。\n"
                "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余文字）。\n"
                "输出 JSON schema（字段允许为空，但必须是合法 JSON）：\n"
                "{\n"
                '  "main_arc": "string",\n'
                '  "themes": ["string"],\n'
                '  "chapters": [\n'
                "    {\n"
                '      "chapter_index": number,\n'
                '      "title": "string",\n'
                '      "goal": "string",\n'
                '      "conflict": "string",\n'
                '      "beats": ["string"],\n'
                '      "ending_hook": "string"\n'
                "    }\n"
                "  ]\n"
                "}\n"
                "要求：\n"
                f"- chapters 必须包含 1..{chapters_total} 每一章（chapter_index 从 1 开始连续）。\n"
                "- 每章 beats 3~6 条，强调可写作的行动/冲突/信息揭露，不要百科式设定说明。\n"
                "- 必须遵守 Canon（若 Canon 不完整，用模糊表达，不要强行新增硬设定名词）。\n"
            )
        )
        human = HumanMessage(
            content=(
                f"项目：{project_name}\n"
                f"点子：{idea}\n"
                f"章节数：{chapters_total}\n"
                + (f"\n策划任务书（主线脉络）：\n{instr}\n" if instr else "")
                + "\n【Canon（真值来源）】\n"
                + f"{canon_text}\n"
            )
        )
        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="screenwriter",
                chapter_index=0,
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
                node="screenwriter",
                chapter_index=0,
                content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
                finish_reason=fr,
                token_usage=usage,
            )
        obj = _extract(text)
        if not obj:
            raise ValueError("screenwriter_agent: 无法从 LLM 输出中提取 JSON")
        state["screenwriter_result"] = obj
        state["screenwriter_used_llm"] = True
        if logger:
            logger.event("node_end", node="screenwriter", chapter_index=0, used_llm=True)
        return state

    # 模板兜底：给最小可用细纲（至少包含第1章）
    state["screenwriter_result"] = {
        "main_arc": "（模板）主线：围绕核心冲突推进，并逐步揭示真相。",
        "themes": ["（模板）成长", "（模板）选择与代价"],
        "chapters": [
            {
                "chapter_index": 1,
                "title": "（模板）风起之时",
                "goal": "主角被卷入事件，必须做出第一个选择。",
                "conflict": "外部压迫与内部犹疑交织。",
                "beats": ["进入新场景", "遭遇阻碍", "抛出钩子/伏笔"],
                "ending_hook": "一个线索指向更大的幕后力量。",
            }
        ],
    }
    state["screenwriter_used_llm"] = False
    if logger:
        logger.event("node_end", node="screenwriter", chapter_index=0, used_llm=False)
    return state


