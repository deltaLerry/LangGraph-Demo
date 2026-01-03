from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from state import StoryState
from debug_log import truncate_text
from storage import build_recent_memory_synopsis, load_canon_bundle, load_recent_chapter_memories, normalize_canon_bundle
from llm_meta import extract_finish_reason_and_usage
from json_utils import extract_first_json_object
from materials import materials_prompt_digest


def _extract_first_json_obj(text: str) -> Dict[str, Any]:
    return extract_first_json_object(text)


def _format_issue_to_text(issue: Dict[str, Any]) -> str:
    t = str(issue.get("type", "") or "").strip() or "N/A"
    canon_key = str(issue.get("canon_key", "") or "").strip() or "N/A"
    quote = str(issue.get("quote", "") or "").strip()
    problem = str(issue.get("issue", "") or "").strip()
    fix = str(issue.get("fix", "") or "").strip()
    action = str(issue.get("action", "") or "").strip() or "rewrite"
    parts = [f"【类型】{t}", f"【CanonKey】{canon_key}", f"【动作】{action}"]
    if quote:
        parts.append(f"【引用】{quote}")
    if problem:
        parts.append(f"【问题】{problem}")
    if fix:
        parts.append(f"【改法】{fix}")
    return " ".join(parts).strip()

def editor_agent(state: StoryState) -> StoryState:
    """
    主编 Agent（审核）
    检查 Writer 输出与 Planner 设定一致性，并返回修改建议
    """
    planner_result = state.get("planner_result")
    writer_result = state.get("writer_result", "")

    if not planner_result or not writer_result:
        raise ValueError("editor_agent: planner_result or writer_result is missing")

    project_name = planner_result.get("项目名称", "")
    issues: list[str] = []

    logger = state.get("logger")
    llm = state.get("llm")
    if llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception as e:  # pragma: no cover
            if state.get("force_llm", False):
                raise RuntimeError(
                    "已指定 LLM 模式，但无法导入 langchain_core.messages（请检查依赖安装/解释器环境）"
                ) from e
            llm = None

    if llm:
        if logger:
            logger.event("node_start", node="editor", chapter_index=state.get("chapter_index", 1))

        # === 2.1：注入 Canon + 最近记忆（控制长度） ===
        chapter_index = int(state.get("chapter_index", 1))
        project_dir = str(state.get("project_dir", "") or "")
        canon0 = load_canon_bundle(project_dir) if project_dir else {"world": {}, "characters": {}, "timeline": {}, "style": ""}
        canon = normalize_canon_bundle(canon0)
        k = int(state.get("memory_recent_k", 3) or 3)
        recent_memories = load_recent_chapter_memories(project_dir, before_chapter=chapter_index, k=k) if project_dir else []
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
            max_chars=6000,
        )
        style_text = truncate_text(str(canon.get("style", "") or ""), max_chars=2000)
        memories_text = truncate_text(build_recent_memory_synopsis(recent_memories), max_chars=1200)

        # === 2.0：阶段3材料包（用于主编审核对照：本章细纲/人物卡/基调） ===
        materials_bundle = state.get("materials_bundle") or {}
        materials_text = ""
        if isinstance(materials_bundle, dict) and materials_bundle:
            materials_text = materials_prompt_digest(materials_bundle, chapter_index=chapter_index)

        system = SystemMessage(
            content=(
                "你是苛刻的编辑部主编，负责最终稿件质量拍板。\n"
                "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余文字）。\n"
                "你要用“反证式审稿”：优先寻找会导致后续崩盘的逻辑漏洞/一致性漏洞/风格硬伤。\n"
                "\n"
                "一致性优先级（硬约束→软约束）：\n"
                "1) Canon 设定（world/characters/timeline/style）：真值来源，任何冲突都算硬伤\n"
                "2) 阶段3【材料包】（人物卡/本章细纲/基调）：必须遵循；但不得覆盖 Canon\n"
                "3) 最近章节记忆：用于连续性；若与 Canon 冲突，以 Canon 为准\n"
                "4) planner 任务：仅参考\n"
                "\n"
                "判定标准（更严格、更有效）：只要命中任一条“硬伤”，必须判定为 审核不通过：\n"
                "- Canon 冲突：事实/规则/角色禁忌/能力/时间线与 Canon 明确不一致\n"
                "- 细纲违背：材料包里“本章 goal/conflict/beats/ending_hook”有明确要求但正文未体现，或推进顺序/因果链明显不成立\n"
                "- 人物不一致：人物言行与人物卡（traits/motivation/taboos）冲突；或关键动机缺失导致行为无因\n"
                "- 内部逻辑漏洞：同章内自相矛盾（上一段说A，下一段说非A）、关键转折缺铺垫、因果断裂\n"
                "- 风格硬伤：明显 AI 总结腔/元话语（例如“作为AI/接下来将…”）、句式机械重复、百科式灌设定导致叙事停滞\n"
                "- 字数硬约束：明显偏离目标区间（过短导致情节不完整/过长导致拖沓）\n"
                "\n"
                "输出质量要求（避免无效审核）：\n"
                "- decision=审核不通过 时，issues 至少 2 条（除非你能确信正文几乎无瑕疵且无硬伤）。\n"
                "- 每条 issue 必须“具体可执行”：指出哪里错 + 为什么错 + 怎么改（改法要能直接照做）。\n"
                "- 每条 issue 必须包含 quote：从正文原样复制一小段，能定位到问题。\n"
                "- 若你找不到可引用 quote，就不要输出该条（宁可少而准）。\n"
                "\n"
                "输出 JSON schema：\n"
                "{\n"
                '  "decision": "审核通过|审核不通过",\n'
                '  "issues": [\n'
                "    {\n"
                '      "type": "world|character|timeline|style|logic|readability",\n'
                '      "canon_key": "string|N/A",\n'
                '      "quote": "string",\n'
                '      "issue": "string",\n'
                '      "fix": "string",\n'
                '      "action": "rewrite|canon_patch",\n'
                '      "canon_patch": {"target":"world.json|characters.json|timeline.json|style.md|N/A","op":"append|note|N/A","path":"string|N/A","value":"any|N/A"}\n'
                "    }\n"
                "  ]\n"
                "}\n"
                "要求：\n"
                "- decision=审核通过 时 issues 为空数组。\n"
                "- decision=审核不通过 时：每条 issue 必须包含 quote（从正文原样复制）。\n"
                "- canon_key：若属于设定冲突/缺失，尽量给出可定位的路径（例如 characters.characters[0].taboos / world.rules[2].name）；否则写 N/A。\n"
                "- action=canon_patch 仅在“确实需要固化进 Canon 且会影响后续一致性”的信息时使用；否则用 rewrite。\n"
                "- 宁可少而准：如果找不到 quote，不要输出该条。"
            )
        )
        human = HumanMessage(
            content=(
                f"项目名称：{project_name}\n"
                f"章节：第{chapter_index}章\n"
                + (f"目标字数：{int(state.get('target_words', 800) or 800)}（约束区间：{int(int(state.get('target_words', 800) or 800)*0.85)}~{int(int(state.get('target_words', 800) or 800)*1.15)}）\n" if True else "")
                f"策划任务（参考）：{planner_result}\n\n"
                "【Canon 设定（真值来源）】\n"
                f"{canon_text}\n\n"
                "【文风约束】\n"
                f"{style_text}\n\n"
                + (
                    ("【阶段3材料包（若提供则用于对照本章细纲/人物卡/基调；不得覆盖 Canon）】\n" + materials_text + "\n\n")
                    if materials_text
                    else ""
                )
                + "【最近章节记忆（参考）】\n"
                f"{memories_text}\n\n"
                "正文：\n"
                f"{writer_result}\n"
            )
        )
        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="editor",
                chapter_index=state.get("chapter_index", 1),
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
                node="editor",
                chapter_index=state.get("chapter_index", 1),
                content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
                finish_reason=finish_reason,
                token_usage=token_usage,
            )
        # 优先按 JSON 解析（推荐路径）
        report = _extract_first_json_obj(text)
        decision = str(report.get("decision", "") or "").strip()
        issues_obj = report.get("issues")
        issues_list: List[Dict[str, Any]] = [x for x in issues_obj if isinstance(x, dict)] if isinstance(issues_obj, list) else []

        # 最新设计：LLM 必须输出严格 JSON；无法解析/字段不合法则直接报错（尽早暴露问题）
        if decision not in ("审核通过", "审核不通过"):
            raise ValueError("editor_agent: LLM 输出不是合法的 editor_report JSON（decision 需为 审核通过/审核不通过）")

        state["editor_report"] = {"decision": decision, "issues": issues_list}
        state["editor_decision"] = decision
        state["editor_used_llm"] = True
        state["needs_rewrite"] = decision != "审核通过"

        # 生成 writer 可用的可读反馈
        state["editor_feedback"] = [_format_issue_to_text(it) for it in issues_list]

        # 分离 canon_suggestions（只落盘，不自动应用）
        canon_suggestions: List[Dict[str, Any]] = []
        for it in issues_list:
            if str(it.get("action", "") or "").strip() == "canon_patch":
                canon_suggestions.append(it)
        state["canon_suggestions"] = canon_suggestions

        if logger:
            logger.event(
                "node_end",
                node="editor",
                chapter_index=state.get("chapter_index", 1),
                used_llm=True,
                editor_decision=str(state.get("editor_decision", "")),
                feedback_count=len(state.get("editor_feedback", []) or []),
                canon_suggestions_count=len(canon_suggestions),
            )
        return state

    # 模板审核：做最基础的一致性与可读性检查
    if logger:
        logger.event("node_start", node="editor", chapter_index=state.get("chapter_index", 1))
    if project_name not in writer_result:
        issues.append(f"正文中未包含项目名称 '{project_name}'，建议添加或调整开篇。")

    # 假设检查开篇基调（示例）：
    opening_style = planner_result.get("任务列表", [])[-1].get("任务指令", "")
    if "轻松" in opening_style and "热血" in writer_result:
        issues.append("正文风格与开篇基调可能不符，建议调整语气。")

    # 如果发现问题，放入 state 供 Writer 重写
    if issues:
        state["editor_decision"] = "审核不通过"
        state["editor_feedback"] = issues
        state["editor_report"] = {
            "decision": "审核不通过",
            "issues": [
                {
                    "type": "readability",
                    "canon_key": "N/A",
                    "quote": "",
                    "issue": x,
                    "fix": "",
                    "action": "rewrite",
                    "canon_patch": {"target": "N/A", "op": "N/A", "path": "N/A", "value": "N/A"},
                }
                for x in issues
            ],
        }
        state["canon_suggestions"] = []
        state["needs_rewrite"] = True
    else:
        state["editor_decision"] = "审核通过"
        state["editor_feedback"] = []
        state["editor_report"] = {"decision": "审核通过", "issues": []}
        state["canon_suggestions"] = []
        state["needs_rewrite"] = False
    state["editor_used_llm"] = False
    if logger:
        logger.event(
            "node_end",
            node="editor",
            chapter_index=state.get("chapter_index", 1),
            used_llm=False,
            editor_decision=str(state.get("editor_decision", "")),
            feedback_count=len(state.get("editor_feedback", []) or []),
        )

    return state

