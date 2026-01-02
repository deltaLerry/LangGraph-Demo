from __future__ import annotations

import json

from state import StoryState
from debug_log import truncate_text
from storage import build_recent_memory_synopsis, load_canon_bundle, load_recent_chapter_memories

_CONFLICTS_MARKER = "CONFLICTS_JSON:"


def _extract_conflicts(text: str) -> tuple[str, list[dict]]:
    """
    从主编输出中抽取 conflicts JSON 附录。
    约定格式：
    - 正文部分：审核通过/审核不通过 + 修改清单
    - 附录：单独一行以 'CONFLICTS_JSON:' 开头，后面紧跟 JSON（可以换行）
    返回：(去掉附录后的文本, conflicts列表)
    """
    raw = (text or "").strip()
    if not raw:
        return raw, []
    if _CONFLICTS_MARKER not in raw:
        return raw, []
    before, after = raw.split(_CONFLICTS_MARKER, 1)
    json_part = after.strip()
    conflicts: list[dict] = []
    if json_part:
        try:
            obj = json.loads(json_part)
            if isinstance(obj, list):
                conflicts = [x for x in obj if isinstance(x, dict)]
            elif isinstance(obj, dict):
                # 允许 {"conflicts":[...]} 或直接 dict
                inner = obj.get("conflicts")
                if isinstance(inner, list):
                    conflicts = [x for x in inner if isinstance(x, dict)]
                else:
                    conflicts = [obj]
        except Exception:
            conflicts = []
    return before.strip(), conflicts


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
                "一致性要求：必须对照 Canon 设定（世界观/人物卡/时间线/文风）与最近章节记忆，指出矛盾点。\n"
                "如果不通过：每条建议尽量包含【冲突依据】+【正文定位（引用原句/段）】+【改法】。"
                "\n\n"
                "额外要求（结构化冲突附录）：\n"
                "如果不通过：在清单之后，另起一行输出下面标记，然后输出 JSON（不要 markdown 代码块）：\n"
                f"{_CONFLICTS_MARKER}\n"
                '[{"type":"world|character|timeline|style","canon_key":"string","quote":"string","fix":"string"}]\n'
                "说明：quote 必须直接引用正文原句/原段；canon_key 尽量写对应的设定键路径/名称；fix 给出可执行改法。\n"
                "如果通过：不要输出附录。"
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
            logger.event(
                "llm_response",
                node="editor",
                chapter_index=state.get("chapter_index", 1),
                content=truncate_text(text, max_chars=getattr(logger, "max_chars", 20000)),
            )
        # 解析 conflicts 附录（只在不通过时要求输出）
        text_main, conflicts = _extract_conflicts(text)
        state["editor_conflicts"] = conflicts

        if text_main.startswith("审核通过"):
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
                    conflicts_count=len(state.get("editor_conflicts", []) or []),
                )
            return state

        # 否则视为不通过：抽取清单
        feedback_lines = []
        for line in text_main.splitlines():
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
                conflicts_count=len(state.get("editor_conflicts", []) or []),
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
    state["editor_conflicts"] = []
    if logger:
        logger.event(
            "node_end",
            node="editor",
            chapter_index=state.get("chapter_index", 1),
            used_llm=False,
            editor_decision=str(state.get("editor_decision", "")),
            feedback_count=len(state.get("editor_feedback", []) or []),
            conflicts_count=len(state.get("editor_conflicts", []) or []),
        )

    return state

