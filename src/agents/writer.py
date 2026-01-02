from __future__ import annotations

import json

from state import StoryState
from debug_log import truncate_text
from storage import build_recent_memory_synopsis, load_canon_bundle, load_recent_chapter_memories

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
        canon = load_canon_bundle(project_dir) if project_dir else {"world": {}, "characters": {}, "timeline": {}, "style": ""}
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

        if is_rewrite:
            system = SystemMessage(
                content=(
                    "你是专业网文写手。你会严格按照主编的具体修改意见对稿件进行重写。\n"
                    "要求：逻辑自洽、避免AI腔、句式多样、节奏紧凑。\n"
                    f"目标字数：约 {target_words} 字。\n"
                    "强约束：不得违背 Canon 设定（世界观/人物卡/时间线/文风）。如发现设定缺失，用模糊表达，不要自创硬设定。\n"
                    "只输出正文，不要额外说明。"
                )
            )
            human = HumanMessage(
                content=(
                    f"项目：{project_name}\n"
                    f"章节：第{chapter_index}章 / 共{chapters_total}章\n"
                    f"点子：{idea}\n"
                    f"开篇基调提示：{opening_task}\n\n"
                    "【Canon 设定（必须遵守）】\n"
                    f"{canon_text}\n\n"
                    "【文风约束（必须遵守）】\n"
                    f"{style_text}\n\n"
                    "【最近章节记忆（参考，避免矛盾）】\n"
                    f"{memories_text}\n\n"
                    "主编修改意见：\n"
                    + "\n".join([f"- {x}" for x in feedback])
                    + "\n\n"
                    "请给出重写后的完整正文："
                )
            )
        else:
            system = SystemMessage(
                content=(
                    "你是专业网文写手，擅长把一个点子写成逻辑通顺、画面感强的短篇开篇。\n"
                    "要求：中文；自然流畅；有冲突与钩子；避免AI感。\n"
                    f"目标字数：约 {target_words} 字。\n"
                    "强约束：不得违背 Canon 设定（世界观/人物卡/时间线/文风）。如发现设定缺失，用模糊表达，不要自创硬设定。\n"
                    "只输出正文，不要标题以外的任何说明。"
                )
            )
            human = HumanMessage(
                content=(
                    f"项目：{project_name}\n"
                    f"章节：第{chapter_index}章 / 共{chapters_total}章\n"
                    f"点子：{idea}\n"
                    f"开篇基调提示：{opening_task}\n\n"
                    "【Canon 设定（必须遵守）】\n"
                    f"{canon_text}\n\n"
                    "【文风约束（必须遵守）】\n"
                    f"{style_text}\n\n"
                    "【最近章节记忆（参考，避免矛盾）】\n"
                    f"{memories_text}\n\n"
                    "请直接输出正文："
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
                resp = llm.invoke([system, human])
        else:
            resp = llm.invoke([system, human])
        state["writer_result"] = (getattr(resp, "content", "") or "").strip()
        if logger:
            logger.event(
                "llm_response",
                node="writer",
                chapter_index=chapter_index,
                writer_version=writer_version,
                content=truncate_text(state["writer_result"], max_chars=getattr(logger, "max_chars", 20000)),
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

