from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from debug_log import truncate_text
from llm_json import invoke_json_with_repair
from materials import ensure_materials_pack, ensure_outline, ensure_tone, ensure_world, ensure_characters
from state import StoryState


def _infer_arcs(outline_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    chs = outline_obj.get("chapters")
    if not isinstance(chs, list):
        return []
    order: List[str] = []
    by: Dict[str, Dict[str, Any]] = {}
    for it in chs:
        if not isinstance(it, dict):
            continue
        arc_id = str(it.get("arc_id", "") or "").strip() or "arc_001"
        arc_title = str(it.get("arc_title", "") or "").strip()
        try:
            ci = int(it.get("chapter_index", 0) or 0)
        except Exception:
            ci = 0
        if ci <= 0:
            continue
        if arc_id not in by:
            by[arc_id] = {"arc_id": arc_id, "arc_title": arc_title, "start_chapter": ci, "end_chapter": ci}
            order.append(arc_id)
        else:
            by[arc_id]["start_chapter"] = min(int(by[arc_id]["start_chapter"]), ci)
            by[arc_id]["end_chapter"] = max(int(by[arc_id]["end_chapter"]), ci)
            if (not str(by[arc_id].get("arc_title", "") or "").strip()) and arc_title:
                by[arc_id]["arc_title"] = arc_title
    return [by[k] for k in order][:30]


def _static_findings(
    *,
    outline: Dict[str, Any],
    tone: Dict[str, Any],
    pack: Dict[str, Any],
    min_decisions: int,
) -> List[Dict[str, str]]:
    """
    轻量静态扫描：把“明显缺口/互相打架风险”提前喂给材料主编，提升收敛质量。
    """
    out: List[Dict[str, str]] = []
    decisions = pack.get("decisions")
    if (not isinstance(decisions, list)) or len(decisions) < int(min_decisions):
        out.append(
            {
                "topic": "收敛口径不足",
                "evidence": f"materials_pack.decisions 条数不足（需要≥{min_decisions}）",
                "impact": "写手/主编会把上游细节当成同等约束，容易互相打架或漂移",
            }
        )
    arcs = _infer_arcs(outline)
    arc_plan = pack.get("arc_plan")
    if arcs and (not isinstance(arc_plan, list) or len(arc_plan) == 0):
        out.append(
            {
                "topic": "卷/副本结构未同步到裁剪层",
                "evidence": "outline 存在 arc_id/arc_title，但 materials_pack.arc_plan 为空",
                "impact": "长程节奏与“卷末收束+抛钩子”难以统一",
            }
        )
    if (not str(tone.get("pacing", "") or "").strip()) and (not str(pack.get("pacing_plan", "") or "").strip()):
        out.append(
            {
                "topic": "节奏规划缺失",
                "evidence": "tone.pacing 与 materials_pack.pacing_plan 均为空或过短",
                "impact": "容易出现前期过快掀底牌或中期松散灌水",
            }
        )
    return out


def materials_pack_loop_agent(state: StoryState) -> StoryState:
    """
    在进入章节 writer/editor 循环前，把 materials_pack 通过“材料写手→材料主编”迭代打磨到更一致可执行。
    - 仅在 LLM 可用时启用；template 模式不运行。
    - 输出写回 state.materials_bundle.materials_pack（不新增文件）。
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="materials_pack_loop", chapter_index=0)

    llm = state.get("llm")
    llm_mode = str(state.get("llm_mode", "auto") or "auto").strip().lower()
    if (not llm) or (llm_mode not in ("llm", "auto")):
        if logger:
            logger.event("node_end", node="materials_pack_loop", chapter_index=0, skipped=True, reason="no_llm")
        return state

    mb = state.get("materials_bundle") if isinstance(state.get("materials_bundle"), dict) else {}
    if not isinstance(mb, dict) or not mb:
        if logger:
            logger.event("node_end", node="materials_pack_loop", chapter_index=0, skipped=True, reason="missing_materials_bundle")
        return state

    pack0 = mb.get("materials_pack") if isinstance(mb.get("materials_pack"), dict) else {}
    if not isinstance(pack0, dict) or not pack0:
        if logger:
            logger.event("node_end", node="materials_pack_loop", chapter_index=0, skipped=True, reason="missing_materials_pack")
        return state

    # 上游材料（用于审视一致性）
    world = ensure_world(mb.get("world"))
    characters = ensure_characters(mb.get("characters"))
    outline = ensure_outline(mb.get("outline"))
    tone = ensure_tone(mb.get("tone"))

    chapters_total = int(state.get("chapters_total", 1) or 1)
    target_words = int(state.get("target_words", 800) or 800)
    user_style = str(state.get("style_override", "") or "").strip()
    paragraph_rules = str(state.get("paragraph_rules", "") or "").strip()
    max_rounds = int(state.get("materials_pack_max_rounds", 2) or 2)
    min_decisions = int(state.get("materials_pack_min_decisions", 1) or 1)
    if max_rounds <= 0:
        if logger:
            logger.event("node_end", node="materials_pack_loop", chapter_index=0, skipped=True, reason="disabled")
        return state

    arcs_hint = _infer_arcs(outline)
    static_findings = _static_findings(outline=outline, tone=tone, pack=pack0, min_decisions=min_decisions)

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
    except Exception:
        if logger:
            logger.event("node_end", node="materials_pack_loop", chapter_index=0, skipped=True, reason="missing_langchain_messages")
        return state

    # --- 1) 材料主编审稿 schema ---
    review_schema = (
        "{\n"
        '  "decision": "pass|revise",\n'
        '  "issues": [\n'
        "    {\n"
        '      "topic": "string",\n'
        '      "type": "conflict|missing|unclear|over_detailed|inconsistent",\n'
        '      "evidence": "string",\n'
        '      "impact": "string",\n'
        '      "required_fix": "string"\n'
        "    }\n"
        "  ],\n"
        '  "suggested_decisions": [\n'
        "    {\n"
        '      "topic": "string",\n'
        '      "decision": "string",\n'
        '      "rationale": "string",\n'
        '      "instructions": ["string"]\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )

    # --- 2) 材料写手重写 schema（沿用 materials_pack schema）---
    pack_schema = (
        "{\n"
        '  "version": "pack_v1",\n'
        '  "logline": "string",\n'
        '  "creative_brief": "string",\n'
        '  "pacing_plan": "string",\n'
        '  "arc_plan": [{"arc_id":"string","arc_title":"string","start_chapter":number,"end_chapter":number,"purpose":"string","stakes_escalation":"string","ending_hook":"string"}],\n'
        '  "world_building": "string",\n'
        '  "growth_system": "string",\n'
        '  "style_guide": {"voice":"string","do":["string"],"dont":["string"]},\n'
        '  "conflicts_found": [{"topic":"string","evidence":"string","impact":"string"}],\n'
        '  "decisions": [{"topic":"string","decision":"string","rationale":"string","instructions":["string"]}],\n'
        '  "checklists": {"global":["string"],"per_arc":["string"],"per_chapter":["string"]},\n'
        '  "risks": [{"risk":"string","symptom":"string","mitigation":"string"}]\n'
        "}\n"
    )

    def _validate_review(obj: Dict[str, Any]) -> str:
        dec = str(obj.get("decision", "") or "").strip().lower()
        if dec not in ("pass", "revise"):
            return "bad_decision"
        issues = obj.get("issues")
        if not isinstance(issues, list):
            return "issues_not_list"
        if dec == "revise" and len(issues) == 0:
            return "revise_but_no_issues"
        return ""

    def _validate_pack(obj: Dict[str, Any]) -> str:
        if not isinstance(obj, dict):
            return "not_dict"
        if not str(obj.get("logline", "") or "").strip():
            return "missing_logline"
        ds = obj.get("decisions")
        if not isinstance(ds, list) or len(ds) < int(min_decisions):
            return "decisions_too_few"
        return ""

    pack = ensure_materials_pack(pack0)
    last_review: Dict[str, Any] = {}
    rounds_used = 0

    def _merge_pack(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        """
        将分段产出的 patch 合并回 materials_pack：
        - 只做浅层 merge（materials_pack 顶层字段足够）
        - list 字段（arc_plan/decisions/risks/等）由 patch 覆盖，以避免“越写越重复”
        """
        if not isinstance(base, dict):
            base = {}
        if not isinstance(patch, dict) or not patch:
            return base
        out = dict(base)
        for k, v in patch.items():
            if v is None:
                continue
            out[k] = v
        return out

    def _chunked_rewrite_pack(
        *,
        llm: Any,
        base_pack: Dict[str, Any],
        issues_obj: Any,
        suggested_obj: Any,
        world: Dict[str, Any],
        characters: Dict[str, Any],
        outline: Dict[str, Any],
        tone: Dict[str, Any],
        arcs_hint: Any,
        node_prefix: str,
    ) -> Dict[str, Any]:
        """
        把 materials_pack 的“重写”拆成多段：减少单次 completion 体积，避免 length 截断。
        不降低质量：每段都给同一套上游材料+主编意见，只是输出字段更聚焦。
        """
        # 分段开关/段数（可从 state 覆盖）
        max_parts = int(state.get("materials_pack_writer_max_parts", 5) or 5)
        max_parts = max(1, min(8, max_parts))

        # --- schemas（每段只产一部分字段）---
        schema_core = (
            "{\n"
            '  "logline": "string",\n'
            '  "creative_brief": "string",\n'
            '  "pacing_plan": "string"\n'
            "}\n"
        )
        schema_arc = (
            "{\n"
            '  "arc_plan": [{"arc_id":"string","arc_title":"string","start_chapter":number,"end_chapter":number,"purpose":"string","stakes_escalation":"string","ending_hook":"string"}]\n'
            "}\n"
        )
        schema_world_growth = (
            "{\n"
            '  "world_building": "string",\n'
            '  "growth_system": "string"\n'
            "}\n"
        )
        schema_style = (
            "{\n"
            '  "style_guide": {"voice":"string","do":["string"],"dont":["string"]}\n'
            "}\n"
        )
        schema_exec = (
            "{\n"
            '  "conflicts_found": [{"topic":"string","evidence":"string","impact":"string"}],\n'
            '  "decisions": [{"topic":"string","decision":"string","rationale":"string","instructions":["string"]}],\n'
            '  "checklists": {"global":["string"],"per_arc":["string"],"per_chapter":["string"]},\n'
            '  "risks": [{"risk":"string","symptom":"string","mitigation":"string"}]\n'
            "}\n"
        )

        def _validate_non_empty(field: str) -> Any:
            def _v(obj: Dict[str, Any]) -> str:
                if not isinstance(obj, dict):
                    return "not_dict"
                if not str(obj.get(field, "") or "").strip():
                    return f"missing_{field}"
                return ""

            return _v

        def _validate_arc(obj: Dict[str, Any]) -> str:
            if not isinstance(obj, dict):
                return "not_dict"
            ap = obj.get("arc_plan")
            if ap is None:
                return ""
            if not isinstance(ap, list):
                return "arc_plan_not_list"
            return ""

        def _validate_exec(obj: Dict[str, Any]) -> str:
            if not isinstance(obj, dict):
                return "not_dict"
            ds = obj.get("decisions")
            if ds is not None and (not isinstance(ds, list)):
                return "decisions_not_list"
            # 不强制 >=min_decisions：允许某一段失败时先保留旧 pack 的 decisions
            return ""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception:
            return base_pack

        sys_base = SystemMessage(
            content=(
                "你是小说项目的“材料写手”。你将根据材料主编的 issues 与 suggested_decisions，分段改写 materials_pack。\n"
                "你必须且仅输出严格 JSON。\n"
                "硬约束：不得与 world/characters/outline/tone 冲突；不确定处写“待补充”，不要胡编专有名词。\n"
                "注意：本次是分段输出，每次只输出 schema 指定的字段；不要输出其它字段。\n"
            )
        )

        common_ctx = (
            f"材料主编 issues：\n{truncate_text(json.dumps(issues_obj or [], ensure_ascii=False, indent=2), max_chars=2200)}\n\n"
            f"材料主编 suggested_decisions：\n{truncate_text(json.dumps(suggested_obj or [], ensure_ascii=False, indent=2), max_chars=2200)}\n\n"
            f"Arc结构（从细纲推断，务必对齐）：\n{truncate_text(json.dumps(arcs_hint, ensure_ascii=False, indent=2), max_chars=1600)}\n\n"
            "上游专家材料（事实/约束来源）：\n"
            f"- world: {truncate_text(json.dumps(world, ensure_ascii=False), max_chars=1200)}\n"
            f"- characters: {truncate_text(json.dumps(characters, ensure_ascii=False), max_chars=1200)}\n"
            f"- outline(main_arc/themes): {truncate_text(json.dumps({'main_arc': outline.get('main_arc',''), 'themes': outline.get('themes',[])}, ensure_ascii=False), max_chars=800)}\n"
            f"- tone: {truncate_text(json.dumps(tone, ensure_ascii=False), max_chars=1200)}\n\n"
            "当前 materials_pack（供你参考；在其基础上改好）：\n"
            f"{truncate_text(json.dumps(base_pack, ensure_ascii=False, indent=2), max_chars=4000)}\n"
        )

        out_pack = dict(base_pack)

        parts: list[tuple[str, str, Any, Any]] = [
            ("core", schema_core, _validate_non_empty("logline"), "请只输出 logline/creative_brief/pacing_plan，保持提纲挈领且可执行。"),
            ("arc", schema_arc, _validate_arc, "请只输出 arc_plan（可为空数组），用于长篇节奏与卷末钩子统一。"),
            ("world_growth", schema_world_growth, _validate_non_empty("world_building"), "请只输出 world_building/growth_system：提炼规则与成长体系口径，避免百科堆砌。"),
            ("style", schema_style, None, "请只输出 style_guide：voice/do/dont 要可执行、可检查，避免泛泛而谈。"),
            ("exec", schema_exec, _validate_exec, "请只输出 conflicts_found/decisions/checklists/risks：收敛口径要清晰、指令要可落地。"),
        ]
        parts = parts[:max_parts]

        for tag, schema_text, validate_fn, instruction in parts:
            human = HumanMessage(
                content=(
                    f"{instruction}\n\n"
                    f"{common_ctx}\n\n"
                    "本段 schema：\n"
                    f"{schema_text}\n"
                )
            )
            obj, _raw, _fr, _usage = invoke_json_with_repair(
                llm=llm,
                messages=[sys_base, human],
                schema_text=schema_text,
                node=f"{node_prefix}_{tag}",
                chapter_index=0,
                logger=logger,
                max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                validate=validate_fn,
                max_fix_chars=12000,
            )
            if isinstance(obj, dict) and obj:
                out_pack = _merge_pack(out_pack, obj)

        # 最终标准化（补默认字段/清洗类型）
        return ensure_materials_pack(out_pack)

    for r in range(max_rounds):
        rounds_used = r + 1
        # (A) 材料主编：找冲突/缺口，给裁决建议
        sys_r = SystemMessage(
            content=(
                "你是小说项目的“材料主编”。你的任务是在进入正文写作前，确保材料包裁剪层（materials_pack）一致、可执行、无冲突。\n"
                "你必须且仅输出严格 JSON。\n"
                "判定标准：\n"
                "- pass：无关键冲突/缺口，且 decisions 足够支撑写作口径；\n"
                "- revise：存在冲突/缺口/不可执行点，需要材料写手改。\n"
            )
        )
        human_r = HumanMessage(
            content=(
                f"规模：总章数={chapters_total}；每章≈{target_words}\n"
                f"用户风格覆盖：{truncate_text(user_style, max_chars=800) or '（无）'}\n"
                f"段落/结构规则：{truncate_text(paragraph_rules, max_chars=800) or '（无）'}\n\n"
                f"Arc结构（从细纲推断）：\n{truncate_text(json.dumps(arcs_hint, ensure_ascii=False, indent=2), max_chars=1600)}\n\n"
                f"静态扫描发现（可作为证据）：\n{truncate_text(json.dumps(static_findings, ensure_ascii=False, indent=2), max_chars=1600)}\n\n"
                "上游专家材料（事实/约束来源）：\n"
                f"- world: {truncate_text(json.dumps(world, ensure_ascii=False), max_chars=1200)}\n"
                f"- characters: {truncate_text(json.dumps(characters, ensure_ascii=False), max_chars=1200)}\n"
                f"- outline(main_arc/themes): {truncate_text(json.dumps({'main_arc': outline.get('main_arc',''), 'themes': outline.get('themes',[])}, ensure_ascii=False), max_chars=800)}\n"
                f"- tone: {truncate_text(json.dumps(tone, ensure_ascii=False), max_chars=1200)}\n\n"
                "当前 materials_pack（需要你审稿）：\n"
                f"{truncate_text(json.dumps(pack, ensure_ascii=False, indent=2), max_chars=4000)}\n"
            )
        )
        review, _raw, _fr, _usage = invoke_json_with_repair(
            llm=llm,
            messages=[sys_r, human_r],
            schema_text=review_schema,
            node="materials_pack_editor",
            chapter_index=0,
            logger=logger,
            max_attempts=int(state.get("llm_max_attempts", 3) or 3),
            base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
            validate=_validate_review,
            max_fix_chars=12000,
        )
        last_review = review if isinstance(review, dict) else {}
        decision = str(last_review.get("decision", "") or "").strip().lower()
        if decision == "pass":
            break

        # (B) 材料写手：按 issues + suggested_decisions 重写 pack
        issues_obj = last_review.get("issues", [])
        suggested_obj = last_review.get("suggested_decisions", [])
        pack2 = _chunked_rewrite_pack(
            llm=llm,
            base_pack=pack,
            issues_obj=issues_obj,
            suggested_obj=suggested_obj,
            world=world,
            characters=characters,
            outline=outline,
            tone=tone,
            arcs_hint=arcs_hint,
            node_prefix="materials_pack_writer",
        )
        if isinstance(pack2, dict) and pack2:
            # 最终再做一次强校验：logline + decisions 数量
            pack2 = ensure_materials_pack(pack2)
            if _validate_pack(pack2) == "":
                pack = pack2
            else:
                # 分段重写没达标：保留旧 pack（避免质量下降）
                if logger:
                    logger.event(
                        "materials_pack_writer_incomplete",
                        node="materials_pack_writer",
                        chapter_index=0,
                        reason=_validate_pack(pack2),
                    )

    mb2 = dict(mb)
    mb2["materials_pack"] = ensure_materials_pack(pack)
    state["materials_bundle"] = mb2
    state["materials_pack_loop_rounds"] = int(rounds_used)
    state["materials_pack_loop_last_review"] = last_review if isinstance(last_review, dict) else {}

    if logger:
        logger.event(
            "node_end",
            node="materials_pack_loop",
            chapter_index=0,
            used_llm=True,
            rounds=int(rounds_used),
            last_decision=str((last_review or {}).get("decision", "") or ""),
        )
    return state


