from __future__ import annotations

import json
from typing import Any, Dict

from state import StoryState
from debug_log import truncate_text
from json_utils import extract_first_json_object
from llm_meta import extract_finish_reason_and_usage
from storage import load_canon_bundle
from llm_call import invoke_with_retry


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
    target_words = int(state.get("target_words", 800) or 800)
    # 支持“分块生成细纲”：通过 state 指定本次只生成一个章节范围（outline_start..outline_end）
    outline_start = int(state.get("outline_start", 1) or 1)
    outline_end = int(state.get("outline_end", chapters_total) or chapters_total)
    outline_start = max(1, outline_start)
    outline_end = max(outline_start, min(int(chapters_total), outline_end))
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

    # 现有细纲提示（用于分块续写时保持连续性；可选）
    outline_hint = ""
    try:
        mb = state.get("materials_bundle")
        if isinstance(mb, dict) and mb:
            out0 = mb.get("outline") if isinstance(mb.get("outline"), dict) else {}
            if isinstance(out0, dict) and out0:
                chs = out0.get("chapters") if isinstance(out0.get("chapters"), list) else []
                last = []
                for it in chs[-5:]:
                    if isinstance(it, dict):
                        last.append(
                            {
                                "chapter_index": it.get("chapter_index"),
                                "title": it.get("title", ""),
                                "ending_hook": it.get("ending_hook", ""),
                            }
                        )
                hint_obj = {
                    "main_arc": out0.get("main_arc", ""),
                    "themes": out0.get("themes", []),
                    "last_chapters": last,
                }
                outline_hint = truncate_text(json.dumps(hint_obj, ensure_ascii=False, indent=2), max_chars=1800)
    except Exception:
        outline_hint = ""

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

        required_range = f"{outline_start}..{outline_end}"
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
                f"- chapters 必须包含 {required_range} 这段范围内的每一章（chapter_index 连续）。\n"
                f"- 全书总章数为 {chapters_total}（用于把控节奏与铺垫），但本次只生成 {required_range} 的细纲。\n"
                "- 每章 beats 3~6 条，强调可写作的行动/冲突/信息揭露，不要百科式设定说明。\n"
                f"- 本次项目规模：总章数={chapters_total}；每章目标字数≈{target_words}（中文字符数近似）。请据此统一节奏：长篇要留足伏笔与层层升级，不要在前几章把底牌全掀完。\n"
                "- 必须遵守 Canon（若 Canon 不完整，用模糊表达，不要强行新增硬设定名词）。\n"
            )
        )
        human = HumanMessage(
            content=(
                f"项目：{project_name}\n"
                f"点子：{idea}\n"
                f"章节数：{chapters_total}\n"
                f"每章目标字数：{target_words}\n"
                + (f"\n策划任务书（主线脉络）：\n{instr}\n" if instr else "")
                + (("\n【已有细纲提示（保持连续性；可参考）】\n" + outline_hint + "\n") if outline_hint else "")
                + "\n【Canon（真值来源）】\n"
                + f"{canon_text}\n"
            )
        )
        resp = _invoke_once("screenwriter", system, human)
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
        fr0, _usage0 = extract_finish_reason_and_usage(resp)

        if (not obj) or (fr0 and str(fr0).lower() == "length"):
            system_retry = SystemMessage(
                content=(
                    "你是小说项目的“编剧”。你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
                    "务必短：确保 JSON 完整可解析。\n"
                    f"硬性约束：chapters 仍需包含 {required_range} 每一章，但每章 beats 只写 2~3 条，每条不超过 35 字。\n"
                    "title/goal/conflict/ending_hook 尽量短。\n"
                    "输出 JSON schema 与上一次相同。\n"
                )
            )
            resp2 = _invoke_once("screenwriter_retry", system_retry, human)
            text2 = (getattr(resp2, "content", "") or "").strip()
            if logger:
                fr2, usage2 = extract_finish_reason_and_usage(resp2)
                logger.event(
                    "llm_response",
                    node="screenwriter_retry",
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
                raise ValueError("screenwriter_agent: 无法从 LLM 输出中提取 JSON（已重试）")
            if logger:
                logger.event("llm_parse_failed", node="screenwriter", chapter_index=0, action="fallback_template")
            llm = None
        else:
            # 兜底校验：若 LLM 没按要求输出完整范围，补齐缺失章，避免后续 writer 拿不到本章细纲
            try:
                chs = obj.get("chapters") if isinstance(obj.get("chapters"), list) else []
                by_idx: dict[int, dict] = {}
                for it in chs:
                    if not isinstance(it, dict):
                        continue
                    try:
                        idx = int(it.get("chapter_index", 0) or 0)
                    except Exception:
                        idx = 0
                    if idx > 0:
                        by_idx[idx] = it
                for i in range(outline_start, outline_end + 1):
                    if i in by_idx:
                        continue
                    by_idx[i] = {
                        "chapter_index": i,
                        "title": f"（占位）第{i}章",
                        "goal": "（占位）推进主线并对齐材料包节奏。",
                        "conflict": "（占位）制造冲突与选择。",
                        "beats": ["（占位）推进事件", "（占位）制造冲突", "（占位）留钩子"],
                        "ending_hook": "（占位）留下可承接的悬念。",
                    }
                obj["chapters"] = [by_idx[i] for i in range(outline_start, outline_end + 1)]
            except Exception:
                pass

            state["screenwriter_result"] = obj
            state["screenwriter_used_llm"] = True
            if logger:
                logger.event("node_end", node="screenwriter", chapter_index=0, used_llm=True)
            return state

    # 模板兜底：给最小可用细纲（至少覆盖本次请求范围）
    chapters = []
    for i in range(outline_start, max(outline_start, int(outline_end)) + 1):
        chapters.append(
            {
                "chapter_index": i,
                "title": f"（模板）第{i}章",
                "goal": "推进主线并制造选择与代价。",
                "conflict": "外部阻力与内部动摇交织。",
                "beats": ["推进事件", "制造冲突", "留钩子"],
                "ending_hook": "留下下一章可承接的悬念。",
            }
        )
    state["screenwriter_result"] = {
        "main_arc": "（模板）主线：围绕核心冲突推进，并逐步揭示真相。",
        "themes": ["（模板）成长", "（模板）选择与代价"],
        "chapters": chapters,
    }
    state["screenwriter_used_llm"] = False
    if logger:
        logger.event("node_end", node="screenwriter", chapter_index=0, used_llm=False)
    return state


