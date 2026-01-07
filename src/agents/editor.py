from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from state import StoryState
from debug_log import truncate_text
from storage import build_recent_memory_synopsis, load_canon_bundle, load_recent_chapter_memories, normalize_canon_bundle
from storage import build_recent_arc_synopsis, load_recent_arc_summaries
from storage import build_canon_text_for_context, infer_arc_start_from_materials_bundle, infer_current_arc_start
from llm_meta import extract_finish_reason_and_usage
from json_utils import extract_first_json_object
from materials import materials_prompt_digest
from llm_call import invoke_with_retry
from llm_json import invoke_json_with_repair


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

        # 重申/复审模式：强制产出可执行 issues（用于驱动重写）
        # - 设计目的：有些模型倾向“直接放过”，导致 issues 为空，重写只能靠泛泛要求
        force_reject_with_issues = bool(state.get("editor_force_reject_with_issues", False))
        # 重申/复审模式：更严格（但不会强制拒稿）；用于提高审稿标准
        # 注意：为了避免流程自锁，建议由上层在“最后一次审稿”关闭该开关。
        strict_mode = bool(state.get("editor_strict_mode", False))

        # === 审稿轮次策略：倒数第二次更严格给更多 issues；最后一次放宽提高通过率 ===
        writer_version = int(state.get("writer_version", 1) or 1)
        max_rewrites = int(state.get("max_rewrites", 1) or 1)
        is_last_review = writer_version >= (1 + max_rewrites)
        is_penultimate_review = (not is_last_review) and (writer_version == max_rewrites)

        # === 2.1：注入 Canon + 最近记忆（控制长度） ===
        chapter_index = int(state.get("chapter_index", 1))
        project_dir = str(state.get("project_dir", "") or "")
        k = int(state.get("memory_recent_k", 3) or 3)
        include_unapproved = bool(state.get("include_unapproved_memories", False))
        arc_every_n = int(state.get("arc_every_n", 10) or 10)
        arc_k = int(state.get("arc_recent_k", 2) or 2)
        arc_start = None
        try:
            mb = state.get("materials_bundle")
            if isinstance(mb, dict) and mb:
                arc_start = infer_arc_start_from_materials_bundle(mb, chapter_index=chapter_index)
        except Exception:
            arc_start = None
        if not arc_start:
            arc_start = infer_current_arc_start(project_dir, chapter_index=chapter_index, arc_every_n=arc_every_n) if project_dir else 1
        recent_memories = (
            load_recent_chapter_memories(
                project_dir,
                before_chapter=chapter_index,
                k=k,
                include_unapproved=include_unapproved,
                min_chapter=arc_start,
            )
            if project_dir
            else []
        )
        arc_text = ""
        if bool(state.get("enable_arc_summary", True)) and project_dir:
            arcs = load_recent_arc_summaries(project_dir, before_chapter=chapter_index, k=arc_k)
            arc_text = truncate_text(build_recent_arc_synopsis(arcs), max_chars=1400)
        canon_text = (
            build_canon_text_for_context(
                project_dir,
                chapter_index=chapter_index,
                arc_every_n=arc_every_n,
                arc_recent_k=arc_k,
                include_unapproved=include_unapproved,
                materials_bundle=(state.get("materials_bundle") if isinstance(state.get("materials_bundle"), dict) else None),
                max_chars=6000,
            )
            if project_dir
            else "（无）"
        )
        memories_text = truncate_text(build_recent_memory_synopsis(recent_memories), max_chars=1200)

        # === 2.0：阶段3材料包（用于主编审核对照：本章细纲/人物卡/基调） ===
        materials_bundle = state.get("materials_bundle") or {}
        materials_text = ""
        if isinstance(materials_bundle, dict) and materials_bundle:
            materials_text = materials_prompt_digest(materials_bundle, chapter_index=chapter_index)

        # editor 稳定性参数：同时用于要求一次性输出足够多 issues
        editor_min_issues = max(0, int(state.get("editor_min_issues", 2) or 2))
        retry_on_invalid = max(0, int(state.get("editor_retry_on_invalid", 1) or 1))
        # 动态目标：倒数第二次尽量多提；最后一次更宽松（仅在拒稿时至少给出少量关键硬伤）
        desired_min_issues = editor_min_issues
        if is_penultimate_review:
            desired_min_issues = max(desired_min_issues, 6)
        if is_last_review:
            # 最后一次：宁可通过；若必须拒稿也只需聚焦硬伤
            desired_min_issues = max(0, min(desired_min_issues, 2))
        # 统一口径：prompt / validate / fallback 必须一致
        # - desired_min_issues 可以被放宽到 0（最后一轮更易通过）
        # - 但若决定“审核不通过”，至少要给 1 条 issue（否则“拒稿却无问题”自相矛盾）
        min_reject_issues = max(1, int(desired_min_issues or 0))

        user_style = truncate_text(str(state.get("style_override", "") or "").strip(), max_chars=1200)
        paragraph_rules = truncate_text(str(state.get("paragraph_rules", "") or "").strip(), max_chars=800)
        rewrite_instructions = truncate_text(str(state.get("rewrite_instructions", "") or "").strip(), max_chars=1600)
        system = SystemMessage(
            content=(
                "你是苛刻的编辑部主编，负责最终稿件质量拍板。\n"
                "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余文字）。\n"
                "你要用“反证式审稿”：优先寻找会导致后续崩盘的逻辑漏洞/一致性漏洞/风格硬伤。\n"
                "\n"
                "一致性优先级（硬约束→软约束）：\n"
                "1) Canon 设定（world/characters/timeline）：真值来源，任何冲突都算硬伤\n"
                "2) 阶段3【材料包】（人物卡/本章细纲/基调/风格约束）：必须遵循；但不得覆盖 Canon\n"
                "3) 最近章节记忆：用于连续性；若与 Canon 冲突，以 Canon 为准\n"
                "4) 重写指导/用户风格覆盖/段落规则：若不与 Canon 冲突，优先执行\n"
                "5) planner 任务：仅参考\n"
                "\n"
                "判定标准（更严格、更有效）：只要命中任一条“硬伤”，必须判定为 审核不通过：\n"
                "- Canon 冲突：事实/规则/角色禁忌/能力/时间线与 Canon 明确不一致\n"
                "- 细纲违背：材料包里“本章 goal/conflict/beats/ending_hook”有明确要求但正文未体现，或推进顺序/因果链明显不成立\n"
                "- 人物不一致：人物言行与人物卡（traits/motivation/taboos）冲突；或关键动机缺失导致行为无因\n"
                "- 内部逻辑漏洞：同章内自相矛盾（上一段说A，下一段说非A）、关键转折缺铺垫、因果断裂\n"
                "- 命名漂移：正文引入大量新专有名词（门派/功法/地名/组织/物品等）且不在 Canon/材料包/已知名词清单中\n"
                "- 风格硬伤：明显 AI 总结腔/元话语（例如“作为AI/接下来将…”）、句式机械重复、百科式灌设定导致叙事停滞\n"
                "- 字数硬约束：明显偏离目标区间（过短导致情节不完整/过长导致拖沓）\n"
                "\n"
                "输出质量要求（避免无效审核）：\n"
                f"- 本次审稿轮次：writer_version={writer_version} / max_rewrites={max_rewrites}。\n"
                + (
                    f"- 【复审强制模式】你必须判定为 审核不通过，并给出不少于 {int(min_reject_issues)} 条 issues（每条必须含 quote/issue/fix/action）。\n"
                    if force_reject_with_issues
                    else ""
                )
                + (
                    "- 【严格模式】请提高审稿标准：只要存在明显可改进项（细纲对齐不足、钩子弱、节奏拖沓、画面/心理不足、AI腔/重复句式、信息倾倒、命名漂移风险），倾向判定为 审核不通过 并给出可执行 issues。\n"
                    if (strict_mode and (not is_last_review) and (not force_reject_with_issues))
                    else ""
                )
                + ("- 这是倒数第二次审稿：请尽可能多给出 issues（建议 6~12 条），把所有会导致下次返工的风险一次性指出。\n" if is_penultimate_review else "")
                + ("- 这是最后一次审稿：请适当放宽标准以提高通过率。只有命中“硬伤”才拒稿；若仅是轻微措辞/润色/可接受的小瑕疵，请直接判定为 审核通过。\n" if is_last_review else "")
                + f"- decision=审核不通过 时，issues 至少 {min_reject_issues} 条（每条必须可执行且包含 quote）。\n"
                "- 每条 issue 必须“具体可执行”：指出哪里错 + 为什么错 + 怎么改（改法要能直接照做）。\n"
                "- 每条 issue 必须包含 quote：从正文原样复制一小段，能定位到问题。\n"
                "- 若你找不到可引用 quote，就不要输出该条（宁可少而准）。\n"
                "- issues 请按严重程度从高到低排序（先硬伤后软伤）。\n"
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
                + (
                    f"目标字数：{int(state.get('target_words', 800) or 800)}"
                    f"（约束区间：{int(int(state.get('target_words', 800) or 800)*float(state.get('writer_min_ratio', 0.75) or 0.75))}~"
                    f"{int(int(state.get('target_words', 800) or 800)*float(state.get('writer_max_ratio', 1.25) or 1.25))}）\n"
                )
                + f"策划任务（参考）：{planner_result}\n\n"
                "【Canon 设定（真值来源）】\n"
                f"{canon_text}\n\n"
                + (("【重写指导（不与 Canon 冲突时最高优先级）】\n" + rewrite_instructions + "\n\n") if rewrite_instructions else "")
                + (("【用户风格覆盖（不与 Canon 冲突时优先执行）】\n" + user_style + "\n\n") if user_style else "")
                + (("【段落/结构约束（不与 Canon 冲突时优先执行）】\n" + paragraph_rules + "\n\n") if paragraph_rules else "")
                + (
                    ("【阶段3材料包（若提供则用于对照本章细纲/人物卡/基调；不得覆盖 Canon）】\n" + materials_text + "\n\n")
                    if materials_text
                    else ""
                )
                + (("【分卷/Arc摘要（参考，优先于单章梗概；避免长程矛盾）】\n" + arc_text + "\n\n") if arc_text else "")
                + "【最近章节记忆（参考）】\n"
                f"{memories_text}\n\n"
                + "正文：\n"
                f"{writer_result}\n"
            )
        )
        schema_text = (
            "{\n"
            '  "decision": "审核通过|审核不通过",\n'
            '  "issues": [\n'
            "    {\n"
            '      "type": "logic|canon|pacing|character|style|readability",\n'
            '      "canon_key": "string|N/A",\n'
            '      "quote": "string",\n'
            '      "issue": "string",\n'
            '      "fix": "string",\n'
            '      "action": "rewrite|canon_patch",\n'
            '      "canon_patch": {"target":"world.json|characters.json|timeline.json|style.md|N/A","op":"append|note|N/A","path":"string|N/A","value":"any|N/A"}\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )

        def _validate(rep: Dict[str, Any]) -> str:
            dec = str(rep.get("decision", "") or "").strip()
            if dec not in ("审核通过", "审核不通过"):
                return "invalid_decision"
            iss = rep.get("issues")
            # 复审强制模式：不允许“审核通过”（必须输出拒稿+足量 issues）
            if force_reject_with_issues and dec == "审核通过":
                return "pass_not_allowed_in_force_reject_mode"
            if dec == "审核通过":
                # 通过时允许 issues 为空（便于提高最后一轮通过率）
                return ""
            # 拒稿：issues 必须是 list 且数量达到期望阈值
            if not isinstance(iss, list):
                return "issues_not_list"
            need_n = int(min_reject_issues)
            if len([x for x in iss if isinstance(x, dict)]) < need_n:
                return f"issues_too_few(expected>={need_n})"
            # 最小字段校验（避免空 issue）
            for i, it in enumerate(iss):
                if not isinstance(it, dict):
                    continue
                if not str(it.get("quote", "") or "").strip():
                    return f"issue_missing_quote(idx={i})"
                if not str(it.get("issue", "") or "").strip():
                    return f"issue_missing_issue(idx={i})"
                if not str(it.get("fix", "") or "").strip():
                    return f"issue_missing_fix(idx={i})"
                if not str(it.get("action", "") or "").strip():
                    return f"issue_missing_action(idx={i})"
            return ""

        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="editor",
                chapter_index=state.get("chapter_index", 1),
                messages=[system, human],
                model=model,
                base_url=str(getattr(llm, "base_url", "") or ""),
            ):
                report, _raw, _fr, _usage = invoke_json_with_repair(
                    llm=llm,
                    messages=[system, human],
                    schema_text=schema_text,
                    node="editor",
                    chapter_index=state.get("chapter_index", 1),
                    logger=logger,
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                    validate=_validate,
                )
        else:
            report, _raw, _fr, _usage = invoke_json_with_repair(
                llm=llm,
                messages=[system, human],
                schema_text=schema_text,
                node="editor",
                chapter_index=state.get("chapter_index", 1),
                logger=None,
                max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                validate=_validate,
            )

        decision = str(report.get("decision", "") or "").strip()
        iss_obj = report.get("issues")
        issues_list: List[Dict[str, Any]] = [x for x in iss_obj if isinstance(x, dict)] if isinstance(iss_obj, list) else []

        # 最终兜底：仍然不合法就降级为“审核不通过 + 结构化最小问题”，避免整次运行退出
        if decision not in ("审核通过", "审核不通过"):
            decision = "审核不通过"
            quote0 = truncate_text(str(writer_result or ""), max_chars=160)
            need_n = int(min_reject_issues)
            issues_list = [
                {
                    "type": "readability",
                    "canon_key": "N/A",
                    "quote": quote0,
                    "issue": "主编输出格式不合法（未能解析为符合 schema 的 JSON）。",
                    "fix": "主编需严格按 schema 只输出 JSON；拒稿时给出多条可执行 issue（每条包含 quote/issue/fix/action）。",
                    "action": "rewrite",
                    "canon_patch": {"target": "N/A", "op": "N/A", "path": "N/A", "value": "N/A"},
                }
            ]
            while len(issues_list) < need_n:
                k2 = len(issues_list) + 1
                issues_list.append(
                    {
                        "type": "readability",
                        "canon_key": "N/A",
                        "quote": quote0,
                        "issue": f"审稿输出不可用（占位补齐 #{k2}）：需要补充具体问题。",
                        "fix": "补充一条带 quote 的具体问题与改法（可从：逻辑因果/人物动机/细纲对齐/节奏拖沓/重复句式/设定灌输 等维度选）。",
                        "action": "rewrite",
                        "canon_patch": {"target": "N/A", "op": "N/A", "path": "N/A", "value": "N/A"},
                    }
                )
            report = {"decision": decision, "issues": issues_list}

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
