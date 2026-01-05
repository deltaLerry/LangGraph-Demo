from __future__ import annotations

from typing import Any, Dict

from state import StoryState
from debug_log import truncate_text
from llm_json import invoke_json_with_repair
from materials import (
    build_materials_bundle,
    ensure_characters,
    ensure_materials_pack,
    ensure_outline,
    ensure_tone,
    ensure_world,
)


def materials_aggregator_agent(state: StoryState) -> StoryState:
    """
    阶段3：材料包汇总器
    - 合并 4 个专家输出（world/characters/outline/tone）
    - 补默认/清洗结构
    - 生成 materials_bundle（写手只吃这一份）
    """
    logger = state.get("logger")
    if logger:
        logger.event("node_start", node="materials_aggregator", chapter_index=0)

    planner_result = state.get("planner_result") or {}
    project_name = str((planner_result or {}).get("项目名称", "") or "")
    idea = str(state.get("user_input", "") or "")

    world_raw = state.get("architect_result") or {}
    chars_raw = state.get("character_director_result") or {}
    outline_raw = state.get("screenwriter_result") or {}
    tone_raw = state.get("tone_result") or {}

    # 清洗（保证结构稳定）
    world = ensure_world(world_raw)
    characters = ensure_characters(chars_raw)
    outline = ensure_outline(outline_raw)
    tone = ensure_tone(tone_raw)

    # === 开题准备包（提纲挈领）：用各专家产出做一次“总编审视” ===
    llm = state.get("llm")
    llm_mode = str(state.get("llm_mode", "auto") or "auto").strip().lower()
    want_llm = bool(llm) and (llm_mode in ("llm", "auto"))
    chapters_total = int(state.get("chapters_total", 1) or 1)
    target_words = int(state.get("target_words", 800) or 800)
    user_style = str(state.get("style_override", "") or "").strip()
    paragraph_rules = str(state.get("paragraph_rules", "") or "").strip()

    def _infer_arcs(outline_obj: Dict[str, Any]) -> list[dict]:
        chs = outline_obj.get("chapters")
        if not isinstance(chs, list):
            return []
        # 按 arc_id 分组（保持顺序）
        order: list[str] = []
        by: dict[str, dict] = {}
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
                # 如果之前没标题，补一下
                if (not str(by[arc_id].get("arc_title", "") or "").strip()) and arc_title:
                    by[arc_id]["arc_title"] = arc_title
        return [by[k] for k in order][:30]

    arcs_hint = _infer_arcs(outline)

    pack: Dict[str, Any] = {}
    if want_llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception:
            want_llm = False
        if want_llm:
            schema_text = (
                "{\n"
                '  "version": "pack_v1",\n'
                '  "logline": "string",\n'
                '  "creative_brief": "string",\n'
                '  "pacing_plan": "string",\n'
                '  "arc_plan": [\n'
                "    {\n"
                '      "arc_id": "string",\n'
                '      "arc_title": "string",\n'
                '      "start_chapter": number,\n'
                '      "end_chapter": number,\n'
                '      "purpose": "string",\n'
                '      "stakes_escalation": "string",\n'
                '      "ending_hook": "string"\n'
                "    }\n"
                "  ],\n"
                '  "world_building": "string",\n'
                '  "growth_system": "string",\n'
                '  "style_guide": {"voice":"string","do":["string"],"dont":["string"]},\n'
                '  "conflicts_found": [{"topic":"string","evidence":"string","impact":"string"}],\n'
                '  "decisions": [{"topic":"string","decision":"string","rationale":"string","instructions":["string"]}],\n'
                '  "checklists": {"global":["string"],"per_arc":["string"],"per_chapter":["string"]},\n'
                '  "risks": [{"risk":"string","symptom":"string","mitigation":"string"}]\n'
                "}\n"
            )
            system = SystemMessage(
                content=(
                    "你是资深总编/策划统筹。请基于各专家材料做一次“开题准备审视”，输出提纲挈领的创作准备包。\n"
                    "目标：让写手在开写前就对【风格】【节奏】【架构】【世界规则/成长体系】【伏笔与风险】心中有数。\n"
                    "你必须显式做“收敛口径”：找出冲突点并给出裁决与可执行指令（decisions）。\n"
                    "要求：\n"
                    "- 只输出一个严格 JSON 对象（不要解释、不要 markdown）。\n"
                    "- 不需要写到具体某一章的细节，但要能指导长篇一致性与推进。\n"
                    "- 内容要具体可执行：给出清晰的写作抓手/检查清单/风险规避。\n"
                    f"- 规模：总章数={chapters_total}；每章目标字数≈{target_words}。\n"
                    "- 约束：必须尊重 Canon/world/characters/outline/tone 与用户风格/段落规则；缺失处用“待补充”占位，不要胡编专有名词。\n"
                )
            )
            human = HumanMessage(
                content=(
                    f"项目名：{project_name}\n"
                    f"点子：{idea}\n"
                    f"用户风格覆盖：{truncate_text(user_style, max_chars=1200) or '（无）'}\n"
                    f"段落/结构规则：{truncate_text(paragraph_rules, max_chars=1200) or '（无）'}\n\n"
                    f"Arc结构（从细纲推断）：\n{truncate_text(__import__('json').dumps(arcs_hint, ensure_ascii=False, indent=2), max_chars=2000)}\n\n"
                    f"世界观（架构师）：\n{truncate_text(__import__('json').dumps(world, ensure_ascii=False, indent=2), max_chars=2500)}\n\n"
                    f"人物（角色导演）：\n{truncate_text(__import__('json').dumps(characters, ensure_ascii=False, indent=2), max_chars=2500)}\n\n"
                    f"主线/细纲（编剧）：\n{truncate_text(__import__('json').dumps({'main_arc': outline.get('main_arc',''), 'themes': outline.get('themes',[]), 'chapters': outline.get('chapters',[])[:20]}, ensure_ascii=False, indent=2), max_chars=2500)}\n\n"
                    f"基调/风格约束（策划）：\n{truncate_text(__import__('json').dumps(tone, ensure_ascii=False, indent=2), max_chars=1800)}\n"
                )
            )

            def _validate(out: Dict[str, Any]) -> str:
                if not isinstance(out, dict):
                    return "not_dict"
                if not str(out.get("logline", "") or "").strip():
                    return "missing_logline"
                if not str(out.get("pacing_plan", "") or "").strip():
                    return "missing_pacing_plan"
                ds = out.get("decisions")
                if not isinstance(ds, list) or len(ds) == 0:
                    return "missing_decisions"
                return ""

            if logger:
                model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
                with logger.llm_call(
                    node="materials_pack",
                    chapter_index=0,
                    messages=[system, human],
                    model=model,
                    base_url=str(getattr(llm, "base_url", "") or ""),
                    extra={"chapters_total": chapters_total, "target_words": target_words},
                ):
                    pack, _raw, _fr, _usage = invoke_json_with_repair(
                        llm=llm,
                        messages=[system, human],
                        schema_text=schema_text,
                        node="materials_pack",
                        chapter_index=0,
                        logger=logger,
                        max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                        base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                        validate=_validate,
                        max_fix_chars=12000,
                    )
            else:
                pack, _raw, _fr, _usage = invoke_json_with_repair(
                    llm=llm,
                    messages=[system, human],
                    schema_text=schema_text,
                    node="materials_pack",
                    chapter_index=0,
                    logger=None,
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                    validate=_validate,
                    max_fix_chars=12000,
                )
    if not isinstance(pack, dict) or not pack:
        # template / fallback：给出最小可用的提纲挈领指导（不追求完整）
        conflicts = []
        decisions = []
        # 极简冲突/裁决：缺什么就先定口径，避免写作阶段互相打架
        if not str(tone.get("narration", "") or "").strip():
            conflicts.append({"topic": "叙事视角/语态未明确", "evidence": "tone.narration 为空", "impact": "容易导致章节视角漂移"})
            decisions.append(
                {
                    "topic": "叙事视角/语态",
                    "decision": "默认第三人称有限视角（跟随主角），必要时用短句内心独白增强代入。",
                    "rationale": "默认口径可减少漂移，且适配大多数网文节奏。",
                    "instructions": ["每章只跟随一个核心视角", "不要全知旁白解释设定"],
                }
            )
        if not arcs_hint:
            conflicts.append({"topic": "卷/副本结构缺失", "evidence": "outline 中缺少 arc_id/arc_title 或章节不足", "impact": "长程节奏难以统一"})
            decisions.append(
                {
                    "topic": "卷/副本结构",
                    "decision": "按每卷10~20章规划副本推进，卷末必须收束+抛新钩子。",
                    "rationale": "用稳定模板约束长篇节奏，避免前期过快或中期松散。",
                    "instructions": ["每卷有明确目标与对手", "卷中段至少一次升级/反转", "卷末写清阶段性胜负+新危机"],
                }
            )
        if not user_style and not tone.get("style_constraints"):
            conflicts.append({"topic": "文风约束不足", "evidence": "用户未提供 style_override 且 tone.style_constraints 为空", "impact": "写作风格不稳定"})
            decisions.append(
                {
                    "topic": "文风统一",
                    "decision": "采用“紧凑叙事+强行动线+结尾钩子”的网文通用口径。",
                    "rationale": "在缺少明确风格时，用高通过率的默认口径保证可写性。",
                    "instructions": ["少设定长解释，多用冲突推动", "每章末尾留可承接悬念"],
                }
            )
        pack = {
            "version": "pack_v1",
            "logline": str(idea or "").strip(),
            "creative_brief": "（模板）本书的核心卖点、矛盾轴与读者爽点需要在此明确：主角目标/阻力/代价/成长线。",
            "pacing_plan": f"（模板）按总章数={chapters_total}与每章≈{target_words}规划：前10%立设定与危机、20%进入副本节奏、持续升级并留钩子。",
            "arc_plan": arcs_hint,
            "world_building": "（模板）提炼世界规则/禁忌/势力格局与冲突驱动，避免百科式堆设定。",
            "growth_system": "（模板）明确成长体系：阶段划分、资源/代价、战力对比口径、升级的剧情触发。",
            "style_guide": {
                "voice": str(tone.get("narration", "") or "").strip(),
                "do": [*([user_style] if user_style else []), "每章都有推进/揭示/反转/钩子之一"],
                "dont": ["重复解释设定", "无推进的闲聊与景物堆砌"],
            },
            "conflicts_found": conflicts,
            "decisions": decisions or [
                {
                    "topic": "统一写作口径（默认）",
                    "decision": "优先保证推进与可读性：行动驱动、冲突升级、信息揭示、结尾钩子。",
                    "rationale": "当上游材料过细或不一致时，用这一层统一标准避免互相打架。",
                    "instructions": ["每章至少一个推进点", "不要为解释设定牺牲推进"],
                }
            ],
            "checklists": {
                "global": ["主线矛盾轴不漂移", "规则与代价一致", "人物动机可追溯"],
                "per_arc": ["本卷目标明确", "中段升级", "卷末收束+抛新钩子"],
                "per_chapter": ["开场入戏", "冲突升级", "信息揭示", "结尾钩子"],
            },
            "risks": [{"risk": "节奏松散", "symptom": "章节推进不足/重复", "mitigation": "每章强制一个推进点+钩子"}],
        }

    pack = ensure_materials_pack(pack)

    bundle = build_materials_bundle(
        project_name=project_name,
        idea=idea,
        world=world,
        characters=characters,
        outline=outline,
        tone=tone,
        materials_pack=pack,
    )
    state["materials_bundle"] = bundle
    state["materials_used_llm"] = bool(
        state.get("architect_used_llm", False)
        or state.get("character_director_used_llm", False)
        or state.get("screenwriter_used_llm", False)
        or state.get("tone_used_llm", False)
    )
    if logger:
        logger.event(
            "node_end",
            node="materials_aggregator",
            chapter_index=0,
            used_llm=bool(state.get("materials_used_llm", False)),
        )
    return state


