from __future__ import annotations

import json

from state import StoryState
from debug_log import truncate_text
from storage import (
    build_recent_arc_synopsis,
    build_recent_memory_synopsis,
    build_canon_text_for_context,
    infer_arc_start_from_materials_bundle,
    infer_current_arc_start,
    load_canon_bundle,
    load_recent_arc_summaries,
    load_recent_chapter_memories,
    normalize_canon_bundle,
)
from llm_meta import extract_finish_reason_and_usage
from materials import materials_prompt_digest
from llm_call import invoke_with_retry

def writer_agent(state: StoryState) -> StoryState:
    """
    写手 Agent：
    - 无 LLM：生成可读的模板正文（用于验证闭环）
    - 有 LLM：根据点子/基调/主编意见生成或重写正文
    """
    planner_result = state.get("planner_result")
    if not planner_result:
        raise ValueError("writer_agent: planner_result is missing")

    logger = state.get("logger")
    project_name = planner_result.get("项目名称", "未命名项目")
    idea = state.get("user_input", "")
    target_words = int(state.get("target_words", 500))
    chapter_index = int(state.get("chapter_index", 1))
    chapters_total = int(state.get("chapters_total", 1))
    writer_version = int(state.get("writer_version", 0)) + 1
    state["writer_version"] = writer_version

    feedback = state.get("editor_feedback") or []
    is_rewrite = bool(state.get("needs_rewrite", False)) and writer_version > 1
    # rewrite 时把“上一版原稿”一并提供给写手，避免只看 issues 导致剧情信息漂移
    draft_text = str(state.get("writer_result", "") or "").strip()

    # 从 Planner 的“开篇基调”任务指令中粗略取出风格关键词（模板/LLM 都可用）
    opening_task = ""
    try:
        opening_task = (planner_result.get("任务列表") or [])[-1].get("任务指令", "")
    except Exception:
        opening_task = ""

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
            logger.event(
                "node_start",
                node="writer",
                chapter_index=chapter_index,
                writer_version=writer_version,
                is_rewrite=is_rewrite,
            )

        # === 2.1：注入 Canon + 最近记忆（控制长度） ===
        project_dir = str(state.get("project_dir", "") or "")
        k = int(state.get("memory_recent_k", 3) or 3)
        include_unapproved = bool(state.get("include_unapproved_memories", False))
        arc_every_n = int(state.get("arc_every_n", 10) or 10)
        arc_k = int(state.get("arc_recent_k", 2) or 2)
        # 优先用“细纲的卷/副本结构（arc_id）”推断本卷范围；失败再回退到 arc summary / 分桶
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
                min_chapter=arc_start,  # 仅注入“本卷/本副本内”的近期记忆，旧卷走 arc_summary
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

        # === 2.0：阶段3材料包（优先于 planner 参考，用于“本章细纲/人物卡/基调”硬约束） ===
        materials_bundle = state.get("materials_bundle") or {}
        materials_text = ""
        if isinstance(materials_bundle, dict) and materials_bundle:
            materials_text = materials_prompt_digest(materials_bundle, chapter_index=chapter_index)

        # === 2.1.1：会议同步摘要（把“主编验收清单/硬约束”同步给写手，提升一次过） ===
        canon0 = load_canon_bundle(project_dir) if project_dir else {"world": {}, "characters": {}, "timeline": {}, "style": ""}
        canon = normalize_canon_bundle(canon0)

        def _canon_names() -> str:
            try:
                names: list[str] = []
                w = canon.get("world") if isinstance(canon.get("world"), dict) else {}
                c = canon.get("characters") if isinstance(canon.get("characters"), dict) else {}
                t = canon.get("timeline") if isinstance(canon.get("timeline"), dict) else {}
                for k in ("rules", "factions", "places"):
                    arr = w.get(k) if isinstance(w.get(k), list) else []
                    for it in arr:
                        if isinstance(it, dict) and str(it.get("name", "") or "").strip():
                            names.append(str(it.get("name")).strip())
                arrc = c.get("characters") if isinstance(c.get("characters"), list) else []
                for it in arrc:
                    if isinstance(it, dict) and str(it.get("name", "") or "").strip():
                        names.append(str(it.get("name")).strip())
                arre = t.get("events") if isinstance(t.get("events"), list) else []
                for it in arre:
                    if isinstance(it, dict):
                        for key in ("event", "name"):
                            if str(it.get(key, "") or "").strip():
                                names.append(str(it.get(key)).strip())
                                break
                # 去重保持顺序
                seen: set[str] = set()
                uniq: list[str] = []
                for n in names:
                    if n in seen:
                        continue
                    seen.add(n)
                    uniq.append(n)
                if not uniq:
                    return "（Canon 里暂无明确的专有名词清单；请尽量避免新增硬设定名词）"
                s = "、".join(uniq[:60])
                return s if len(uniq) <= 60 else s + "…"
            except Exception:
                return "（无法提取 Canon 名词；请尽量避免新增硬设定名词）"

        user_style = str(state.get("style_override", "") or "").strip()
        paragraph_rules = str(state.get("paragraph_rules", "") or "").strip()
        rewrite_instructions = str(state.get("rewrite_instructions", "") or "").strip()
        sync_digest = (
            "【会议同步（写前对齐）｜主编验收清单】\n"
            "- 字数：严格控制在区间内；接近上限要主动收束并结尾。\n"
            "- 设定：只使用 Canon 中已出现的专有名词/势力/地点/能力名；如必须引入新概念，用模糊描述，不要起新名字。\n"
            "- 一致性：人物动机/能力/时间线不要前后打架；不要出现‘上一段说A，下一段又说非A’。\n"
            "- 信息揭露：避免大段设定说明（百科式讲解）；设定通过行动/冲突/对话自然露出。\n"
            "- 风格：以【材料包.tone】为主（style_constraints/avoid）；避免 AI 总结句、机械重复。\n"
            "\n【Canon 已知专有名词（尽量只用这些）】\n"
            f"{_canon_names()}\n"
        )
        if user_style:
            sync_digest += "\n【用户风格覆盖（最高优先级；不允许与 Canon 冲突）】\n" + truncate_text(user_style, max_chars=1200) + "\n"
        if paragraph_rules:
            sync_digest += "\n【段落/结构约束（尽量遵守）】\n" + truncate_text(paragraph_rules, max_chars=800) + "\n"
        if rewrite_instructions:
            sync_digest += "\n【重写指导（最高优先级；不允许与 Canon 冲突）】\n" + truncate_text(rewrite_instructions, max_chars=1600) + "\n"

        # === 2.1.2：结构化审稿意见（优先使用 editor_report.issues） ===
        def _structured_editor_issues_digest() -> str:
            rep = state.get("editor_report")
            if not isinstance(rep, dict):
                return ""
            issues0 = rep.get("issues")
            if not isinstance(issues0, list) or not issues0:
                return ""
            lines: list[str] = []
            for i, it in enumerate(issues0, start=1):
                if not isinstance(it, dict):
                    continue
                t = str(it.get("type", "") or "").strip() or "N/A"
                canon_key = str(it.get("canon_key", "") or "").strip() or "N/A"
                quote = str(it.get("quote", "") or "").strip()
                issue = str(it.get("issue", "") or "").strip()
                fix = str(it.get("fix", "") or "").strip()
                action = str(it.get("action", "") or "").strip() or "rewrite"
                if not issue and not fix and not quote:
                    continue
                lines.append(f"### Issue {i}（{t} / action={action} / canon_key={canon_key}）")
                if quote:
                    lines.append("【证据（quote）】")
                    lines.append(quote)
                if issue:
                    lines.append("【问题】")
                    lines.append(issue)
                if fix:
                    lines.append("【改法】")
                    lines.append(fix)
                lines.append("")  # 分隔
            s = "\n".join(lines).strip()
            return truncate_text(s, max_chars=4500)

        structured_issues_text = _structured_editor_issues_digest()

        if is_rewrite:
            system = SystemMessage(
                content=(
                    "你是专业网文写手。你会严格按照主编的具体修改意见对稿件进行重写。\n"
                    "要求：逻辑自洽、避免AI腔、句式多样、节奏紧凑。\n"
                    f"字数硬性要求：总长度控制在 {int(target_words*float(state.get('writer_min_ratio', 0.75) or 0.75))}~{int(target_words*float(state.get('writer_max_ratio', 1.25) or 1.25))} 字（中文字符数近似，包含标点与空白）。\n"
                    "强约束：不得违背 Canon 设定（世界观/人物卡/时间线/文风）。如发现设定缺失，用模糊表达，不要自创硬设定。\n"
                    "阶段3强约束：若提供了【材料包】，必须遵循其中的“本章细纲/人物卡/基调”。材料包不得与 Canon 冲突；如冲突以 Canon 为准。\n"
                    "命名纪律（长跑一致性关键）：除非 Canon/材料包/已知专有名词清单里已有，否则不要新增门派/功法/地名/组织/物品等专有名词；必须引入新概念时，用模糊描述，不要起新名字。\n"
                    "长章结构（面向可交付）：用“场景推进”写作，每个场景必须有冲突/信息/选择的推进；结尾必须有可承接的钩子。\n"
                    "额外要求：请遵守“会议同步（写前对齐）｜主编验收清单”，目标是一次过审。\n"
                    "执行要求：必须逐条修复【结构化审稿意见】中的每一条 issue；如果某条无法直接修复，需用改写方式规避其触发条件（但最终仍需满足 Canon/材料包）。\n"
                    "写作策略：写到字数区间上限附近请主动收束并结尾，不要超出上限。\n"
                    "只输出正文，不要额外说明。"
                )
            )
            human = HumanMessage(
                content=(
                    f"项目：{project_name}\n"
                    f"章节：第{chapter_index}章 / 共{chapters_total}章\n"
                    f"点子：{idea}\n"
                    f"开篇基调提示：{opening_task}\n\n"
                    f"{sync_digest}\n"
                    + (
                        ("【阶段3材料包（必须遵循；如与 Canon 冲突以 Canon 为准）】\n" + materials_text + "\n\n")
                        if materials_text
                        else ""
                    )
                    + "【Canon 设定（必须遵守）】\n"
                    + f"{canon_text}\n\n"
                    + (("【分卷/Arc摘要（参考，优先于单章梗概；避免长程矛盾）】\n" + arc_text + "\n\n") if arc_text else "")
                    + "【最近章节记忆（参考，避免矛盾）】\n"
                    + f"{memories_text}\n\n"
                    + (("【原稿正文（基于此重写；尽量保持剧情信息与推进，只修复问题并优化表达）】\n" + draft_text + "\n\n") if draft_text else "")
                    + (
                        ("【结构化审稿意见（逐条修复；优先）】\n" + structured_issues_text + "\n\n")
                        if structured_issues_text
                        else ("主编修改意见：\n" + "\n".join([f"- {x}" for x in feedback]) + "\n\n")
                    )
                    + "请给出重写后的完整正文："
                )
            )
        else:
            system = SystemMessage(
                content=(
                    "你是专业网文写手，擅长把一个点子写成逻辑通顺、画面感强的短篇开篇。\n"
                    "要求：中文；自然流畅；有冲突与钩子；避免AI感。\n"
                    f"字数硬性要求：总长度控制在 {int(target_words*float(state.get('writer_min_ratio', 0.75) or 0.75))}~{int(target_words*float(state.get('writer_max_ratio', 1.25) or 1.25))} 字（中文字符数近似，包含标点与空白）。\n"
                    "强约束：不得违背 Canon 设定（世界观/人物卡/时间线/文风）。如发现设定缺失，用模糊表达，不要自创硬设定。\n"
                    "阶段3强约束：若提供了【材料包】，必须遵循其中的“本章细纲/人物卡/基调”。材料包不得与 Canon 冲突；如冲突以 Canon 为准。\n"
                    "命名纪律（长跑一致性关键）：除非 Canon/材料包/已知专有名词清单里已有，否则不要新增门派/功法/地名/组织/物品等专有名词；必须引入新概念时，用模糊描述，不要起新名字。\n"
                    "长章结构（面向可交付）：用“场景推进”写作，每个场景必须有冲突/信息/选择的推进；结尾必须有可承接的钩子。\n"
                    "额外要求：请遵守“会议同步（写前对齐）｜主编验收清单”，目标是一次过审。\n"
                    "写作策略：写到字数区间上限附近请主动收束并结尾，不要超出上限。\n"
                    "只输出正文，不要标题以外的任何说明。"
                )
            )
            human = HumanMessage(
                content=(
                    f"项目：{project_name}\n"
                    f"章节：第{chapter_index}章 / 共{chapters_total}章\n"
                    f"点子：{idea}\n"
                    f"开篇基调提示：{opening_task}\n\n"
                    f"{sync_digest}\n"
                    + (
                        ("【阶段3材料包（必须遵循；如与 Canon 冲突以 Canon 为准）】\n" + materials_text + "\n\n")
                        if materials_text
                        else ""
                    )
                    + "【Canon 设定（必须遵守）】\n"
                    f"{canon_text}\n\n"
                    + (("【分卷/Arc摘要（参考，优先于单章梗概；避免长程矛盾）】\n" + arc_text + "\n\n") if arc_text else "")
                    + "【最近章节记忆（参考，避免矛盾）】\n"
                    f"{memories_text}\n\n"
                    + "请直接输出正文："
                )
            )
        if logger:
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="writer",
                chapter_index=chapter_index,
                messages=[system, human],
                model=model,
                base_url=str(getattr(llm, "base_url", "") or ""),
                extra={"writer_version": writer_version, "is_rewrite": is_rewrite},
            ):
                resp = invoke_with_retry(
                    llm,
                    [system, human],
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                    logger=logger,
                    node="writer",
                    chapter_index=chapter_index,
                    extra={"writer_version": writer_version, "is_rewrite": is_rewrite},
                )
        else:
            resp = invoke_with_retry(
                llm,
                [system, human],
                max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
            )
        text0 = (getattr(resp, "content", "") or "").strip()
        finish_reason, token_usage = extract_finish_reason_and_usage(resp)
        state["writer_result"] = text0
        if logger:
            logger.event(
                "llm_response",
                node="writer",
                chapter_index=chapter_index,
                writer_version=writer_version,
                content=truncate_text(state["writer_result"], max_chars=getattr(logger, "max_chars", 20000)),
                finish_reason=finish_reason,
                token_usage=token_usage,
            )

        # === 字数硬约束 & 被截断自动补全 ===
        # 说明：这里的“字数”按中文字符数近似（含标点/空白），用于工程约束，不追求严格统计口径。
        target = int(state.get("target_words", 800))
        min_ratio = float(state.get("writer_min_ratio", 0.75) or 0.75)
        max_ratio = float(state.get("writer_max_ratio", 1.25) or 1.25)
        min_chars = int(target * min_ratio)
        max_chars = int(target * max_ratio)

        def _need_continue(fr: str | None, s: str) -> bool:
            if fr and fr.lower() == "length":
                return True
            return len(s) < min_chars

        def _need_shorten(s: str) -> bool:
            return len(s) > max_chars

        # 1) length 截断/过短：续写补全（最多2段，避免死循环）
        if _need_continue(finish_reason, state["writer_result"]):
            cur = state["writer_result"]
            for _ in range(2):
                remaining = max(200, target - len(cur))
                # 取末尾上下文，避免重复
                tail = cur[-1200:] if len(cur) > 1200 else cur
                system2 = SystemMessage(
                    content=(
                        "你是专业网文写手。请继续写作补全正文。\n"
                        "要求：延续当前文风与叙事，不要复述已写内容，不要改变既有设定。\n"
                        f"本次请补写约 {remaining} 字（中文字符数近似），并以一个自然段落结尾。\n"
                        "只输出新增内容，不要标题，不要解释。"
                    )
                )
                human2 = HumanMessage(
                    content=(
                        f"项目：{project_name}\n"
                        f"章节：第{chapter_index}章 / 共{chapters_total}章\n\n"
                        "已写正文末尾（用于承接）：\n"
                        f"{tail}\n\n"
                        "请从末尾自然续写："
                    )
                )
                if logger:
                    model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
                    with logger.llm_call(
                        node="writer_continue",
                        chapter_index=chapter_index,
                        messages=[system2, human2],
                        model=model,
                        base_url=str(getattr(llm, "base_url", "") or ""),
                        extra={"writer_version": writer_version},
                    ):
                        resp2 = invoke_with_retry(
                            llm,
                            [system2, human2],
                            max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                            base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                            logger=logger,
                            node="writer_continue",
                            chapter_index=chapter_index,
                            extra={"writer_version": writer_version},
                        )
                else:
                    resp2 = invoke_with_retry(
                        llm,
                        [system2, human2],
                        max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                        base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                    )
                add = (getattr(resp2, "content", "") or "").strip()
                fr2, usage2 = extract_finish_reason_and_usage(resp2)
                if logger:
                    logger.event(
                        "llm_response",
                        node="writer_continue",
                        chapter_index=chapter_index,
                        writer_version=writer_version,
                        content=truncate_text(add, max_chars=getattr(logger, "max_chars", 20000)),
                        finish_reason=fr2,
                        token_usage=usage2,
                    )
                if add:
                    # 简单去重：避免重复粘贴尾部
                    if add in cur:
                        break
                    cur = (cur.rstrip() + "\n\n" + add.lstrip()).strip()
                # 如果已经够长或没有 length 截断，就结束
                if len(cur) >= min_chars and (fr2 is None or fr2.lower() != "length"):
                    break
            state["writer_result"] = cur

        # 2) 超长：自动做一次“缩稿到上限内”，显著提升主编一次通过率
        if _need_shorten(state["writer_result"]):
            if logger:
                logger.event(
                    "writer_length_warning",
                    chapter_index=chapter_index,
                    writer_version=writer_version,
                    target_chars=target,
                    min_chars=min_chars,
                    max_chars=max_chars,
                    actual_chars=len(state.get("writer_result", "") or ""),
                )
            # 只做一次缩稿，避免反复打磨带来风格漂移
            cur = state["writer_result"]
            system3 = SystemMessage(
                content=(
                    "你是专业网文写手兼资深改稿编辑。请将给定正文压缩到指定字数上限内。\n"
                    "要求：\n"
                    "- 不改变事件顺序与关键信息（人物动机/因果链/关键伏笔必须保留）\n"
                    "- 不新增任何设定名词与情节\n"
                    "- 语言更凝练，删掉重复描写、过长比喻、无推进作用的段落\n"
                    f"- 输出必须 <= {max_chars} 字（中文字符数近似，包含标点与空白）\n"
                    "只输出压缩后的正文，不要解释。\n"
                )
            )
            human3 = HumanMessage(
                content=(
                    f"项目：{project_name}\n"
                    f"章节：第{chapter_index}章 / 共{chapters_total}章\n"
                    f"当前长度：{len(cur)}\n"
                    f"目标上限：{max_chars}\n\n"
                    "原文：\n"
                    f"{cur}\n"
                )
            )
            if logger:
                model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
                with logger.llm_call(
                    node="writer_shorten",
                    chapter_index=chapter_index,
                    messages=[system3, human3],
                    model=model,
                    base_url=str(getattr(llm, "base_url", "") or ""),
                    extra={"writer_version": writer_version},
                ):
                    resp3 = invoke_with_retry(
                        llm,
                        [system3, human3],
                        max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                        base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                        logger=logger,
                        node="writer_shorten",
                        chapter_index=chapter_index,
                        extra={"writer_version": writer_version},
                    )
            else:
                resp3 = invoke_with_retry(
                    llm,
                    [system3, human3],
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                )
            shrunk = (getattr(resp3, "content", "") or "").strip()
            fr3, usage3 = extract_finish_reason_and_usage(resp3)
            if shrunk:
                state["writer_result"] = shrunk
            if logger:
                logger.event(
                    "llm_response",
                    node="writer_shorten",
                    chapter_index=chapter_index,
                    writer_version=writer_version,
                    content=truncate_text(state.get("writer_result", ""), max_chars=getattr(logger, "max_chars", 20000)),
                    finish_reason=fr3,
                    token_usage=usage3,
                )

        state["writer_used_llm"] = True
        if logger:
            logger.event(
                "node_end",
                node="writer",
                chapter_index=chapter_index,
                used_llm=True,
                writer_version=writer_version,
                writer_chars=len(state.get("writer_result", "") or ""),
            )
        return state

    # 模板模式：写一段可读开篇（用于闭环验证）
    if logger:
        logger.event(
            "node_start",
            node="writer",
            chapter_index=chapter_index,
            writer_version=writer_version,
            is_rewrite=is_rewrite,
        )
    tone_hint = "紧张" if ("悬疑" in opening_task or "暗黑" in opening_task) else "热血"
    content = (
        f"{project_name}\n\n"
        f"第{chapter_index}章\n\n"
        f"{idea.strip()}。\n"
        f"他原以为这只是一次普通的意外，却在睁眼的瞬间，听见陌生的风从山门外灌进来。"
        f"空气里有淡淡的药香与铁锈味，像是刚经历过一场不见血的厮杀。\n\n"
        f"“新来的？”有人拦住他，目光像刀，落在他手背那道忽然浮现的纹路上。"
        f"那纹路一闪即灭，却让周围几个弟子同时收紧了呼吸。\n\n"
        f"这座宗门不欢迎外人，更不欢迎带着秘密的人。"
        f"而他连自己为什么会出现在这里都说不清，只能硬着头皮往前走。"
        f"从这一刻起，{tone_hint}的齿轮开始转动：\n"
        f"他被迫站队、被迫修行、被迫在每一句客套的笑里辨认杀意。\n\n"
        f"远处钟声响起，像是在宣告某个仪式的开始。"
        f"他不知道那是入门礼，还是审判。"
    )
    state["writer_result"] = content.strip()
    state["writer_used_llm"] = False
    if logger:
        logger.event(
            "node_end",
            node="writer",
            chapter_index=chapter_index,
            used_llm=False,
            writer_version=writer_version,
            writer_chars=len(state.get("writer_result", "") or ""),
        )
    return state

