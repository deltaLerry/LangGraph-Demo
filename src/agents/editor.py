from __future__ import annotations

import json

from state import StoryState
from debug_log import truncate_text
from storage import build_recent_memory_synopsis, load_canon_bundle, load_recent_chapter_memories
from llm_meta import extract_finish_reason_and_usage

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

        system = SystemMessage(
            content=(
                "你是苛刻的编辑部主编，负责最终稿件质量拍板。\n"
                "你必须严格遵守输出规则：\n"
                "- 通过：只输出“审核通过”四个字\n"
                "- 不通过：第一行输出“审核不通过”，并另起一段用清单列出具体修改意见\n"
                "要求：修改意见必须极其具体、可执行。\n"
                "一致性优先级：\n"
                "1) 先对照 Canon 设定（world/characters/timeline/style），这是“真值来源”\n"
                "2) 再对照最近章节记忆（用于情节连续性）\n"
                "3) planner 任务仅作参考（不可覆盖 Canon）\n"
                "如果不通过：每条修改意见必须使用下面模板（请直接写在同一条 bullet 里）：\n"
                "- 【类型】world|character|timeline|style|logic|readability\n"
                "  【CanonKey】（若是设定冲突必填，例如 characters.characters[0].taboos 或 world.rules[2].name；非设定冲突可写 N/A）\n"
                "  【引用】从正文中直接复制 1 句或 1 段原文（必须原样引用）\n"
                "  【问题】简明指出矛盾/不一致/问题点\n"
                "  【改法】给出可执行改写方案（尽量具体到怎么改一句/哪段增删）\n"
                "注意：如果找不到引用原文，请不要输出该条（宁可少而准）。"
            )
        )
        human = HumanMessage(
            content=(
                f"项目名称：{project_name}\n"
                f"策划任务（参考）：{planner_result}\n\n"
                "【Canon 设定（真值来源）】\n"
                f"{canon_text}\n\n"
                "【文风约束】\n"
                f"{style_text}\n\n"
                "【最近章节记忆（参考）】\n"
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
        if text.startswith("审核通过"):
            state["editor_decision"] = "审核通过"
            state["editor_feedback"] = []
            state["needs_rewrite"] = False
            state["editor_used_llm"] = True
            if logger:
                logger.event(
                    "node_end",
                    node="editor",
                    chapter_index=state.get("chapter_index", 1),
                    used_llm=True,
                    editor_decision="审核通过",
                    feedback_count=0,
                )
            return state

        # 否则视为不通过：抽取清单
        feedback_lines = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s == "审核不通过":
                continue
            if s.startswith(("-", "•", "*")):
                feedback_lines.append(s.lstrip("-•* ").strip())
            else:
                feedback_lines.append(s)
        state["editor_decision"] = "审核不通过"
        state["editor_feedback"] = [x for x in feedback_lines if x]
        state["needs_rewrite"] = True
        state["editor_used_llm"] = True
        if logger:
            logger.event(
                "node_end",
                node="editor",
                chapter_index=state.get("chapter_index", 1),
                used_llm=True,
                editor_decision="审核不通过",
                feedback_count=len(state.get("editor_feedback", []) or []),
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
        state["needs_rewrite"] = True
    else:
        state["editor_decision"] = "审核通过"
        state["editor_feedback"] = []
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

