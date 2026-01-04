from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, List

from state import StoryState
from debug_log import truncate_text
from llm_meta import extract_finish_reason_and_usage
from json_utils import extract_first_json_object
from llm_call import invoke_with_retry
from llm_json import invoke_json_with_repair


def _extract_first_json_obj(text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(text)
    return obj if isinstance(obj, dict) else {}


def _looks_like_idea_pack(text: str) -> bool:
    """
    启发式：判断 idea-file 是否是“点子包”（包含书名/文风/段落规则等），而不只是纯点子一句话。
    """
    s = (text or "").strip()
    if not s:
        return False
    # 有明显的键值/标题
    keywords = [
        "项目名称",
        "小说名称",
        "书名",
        "文风",
        "风格",
        "段落",
        "paragraph",
        "style",
        "##",
        "---",
    ]
    hit = sum(1 for k in keywords if k.lower() in s.lower())
    return hit >= 2 or ("项目名称" in s and "点子" in s) or ("文风" in s and "点子" in s)


def _parse_idea_pack_fallback(text: str) -> Dict[str, str]:
    """
    无 LLM 兜底：从常见 Markdown/键值格式中抽取：
    - project_name
    - idea
    - style_override
    - paragraph_rules
    """
    s = (text or "").strip()
    if not s:
        return {"project_name": "", "idea": "", "style_override": "", "paragraph_rules": ""}

    # 0) 支持“块标签”格式：点子：<多行> / 段落规则：<多行>
    #    规则：从 `标签:` 起，读取后续行，直到遇到下一个顶层标签或 EOF。
    top_labels = ("项目名称", "小说名称", "书名", "标题", "文风", "风格", "段落规则", "段落风格", "点子", "创意", "概要")

    def _extract_block(label: str) -> str:
        lines = s.splitlines()
        start_i: int | None = None
        first_line_value = ""
        for i, line in enumerate(lines):
            m = re.match(rf"^\s*{re.escape(label)}\s*[:：]\s*(.*)\s*$", line.strip())
            if not m:
                continue
            start_i = i
            first_line_value = (m.group(1) or "").strip()
            break
        if start_i is None:
            return ""
        buf: List[str] = []
        if first_line_value:
            buf.append(first_line_value)
        for j in range(start_i + 1, len(lines)):
            ln = lines[j]
            if re.match(rf"^\s*(?:{'|'.join([re.escape(x) for x in top_labels])})\s*[:：]\s*", ln.strip()):
                break
            buf.append(ln.rstrip())
        return "\n".join(buf).strip()

    # 1) 键值行：xxx: yyy / xxx：yyy
    kv = {}
    for line in s.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(项目名称|小说名称|书名|标题|文风|风格|段落规则|段落风格|paragraph_rules|style_override)\s*[:：]\s*(.+)$", line, re.I)
        if not m:
            continue
        k = m.group(1).strip().lower()
        v = m.group(2).strip()
        kv[k] = v

    def _get_k(*names: str) -> str:
        for n in names:
            for kk, vv in kv.items():
                if kk == n.lower():
                    return vv
        return ""

    project_name = _get_k("项目名称", "小说名称", "书名", "标题")
    style_override = _get_k("文风", "风格", "style_override")
    paragraph_rules = _get_k("段落规则", "段落风格", "paragraph_rules")

    # 块提取优先：适配 “段落规则：<多行> / 点子：<多行>”
    if not paragraph_rules:
        paragraph_rules = _extract_block("段落规则") or _extract_block("段落风格") or ""

    # 2) Markdown 标题块：## 文风 / ## 段落规则 / ## 点子
    def _extract_section(title: str) -> str:
        # 匹配：## title ... (到下一个 ## 或 EOF)
        pattern = rf"(?im)^\s*##\s*{re.escape(title)}\s*$"
        m0 = re.search(pattern, s)
        if not m0:
            return ""
        start = m0.end()
        rest = s[start:]
        m1 = re.search(r"(?im)^\s*##\s+.+$", rest)
        chunk = rest[: m1.start()] if m1 else rest
        return chunk.strip()

    idea = _extract_section("点子") or _extract_section("创意") or _extract_section("概要") or ""
    if not style_override:
        style_override = _extract_section("文风") or _extract_section("风格") or ""
    if not paragraph_rules:
        paragraph_rules = _extract_section("段落规则") or _extract_section("段落风格") or ""
    if not project_name:
        project_name = _extract_section("小说名称") or _extract_section("项目名称") or _extract_section("书名") or ""

    # 2.5) “点子：<多行>”块（非 markdown 标题）
    if not idea:
        idea = _extract_block("点子") or _extract_block("创意") or _extract_block("概要") or ""

    # 3) 兜底：如果仍没有明确“点子”段，就把全文扣掉已抽取键值行当作 idea
    if not idea:
        # 去掉 kv 行
        filtered_lines = []
        for line in s.splitlines():
            if re.match(r"^(项目名称|小说名称|书名|标题|文风|风格|段落规则|段落风格)\s*[:：]", line.strip()):
                continue
            filtered_lines.append(line)
        idea = "\n".join(filtered_lines).strip()

    return {
        "project_name": project_name.strip(),
        "idea": idea.strip(),
        "style_override": style_override.strip(),
        "paragraph_rules": paragraph_rules.strip(),
    }


def planner_agent(state: StoryState) -> StoryState:
    """
    策划 Agent：
    - 无 LLM：使用模板输出（可跑通工作流）
    - 有 LLM：严格要求只输出 JSON，并解析为 dict
    """
    # === idea-file “点子包”解析（属于策划/planner 职责） ===
    # 说明：main.py 会把 idea-file 原文放到 idea_source_text 中；若为空则退化为 user_input。
    raw_text = str(state.get("idea_source_text", "") or "").strip()
    idea = str(state.get("user_input", "默认点子") or "").strip()
    # 允许用户直接用 --idea 传入“点子包格式”，此时也应触发解析
    raw_for_intake = raw_text or idea
    if raw_text:
        idea = raw_text

    tasks: List[Dict[str, str]] = []
    try:
        raw_tasks = state.get("planner_tasks") or []
        if isinstance(raw_tasks, list):
            for it in raw_tasks:
                if not isinstance(it, dict):
                    continue
                tn = str(it.get("task_name", "") or "").strip()
                ex = str(it.get("executor", "") or "").strip()
                hint = str(it.get("hint", "") or "").strip()
                if tn and ex:
                    tasks.append({"task_name": tn, "executor": ex, "hint": hint})
    except Exception:
        tasks = []
    if not tasks:
        tasks = [
            {"task_name": "世界观设定", "executor": "架构师", "hint": ""},
            {"task_name": "核心角色", "executor": "角色导演", "hint": ""},
            {"task_name": "主线脉络", "executor": "编剧", "hint": ""},
            {"task_name": "开篇基调", "executor": "策划", "hint": ""},
        ]
    logger = state.get("logger")
    chapter_index = state.get("chapter_index", 0)
    if logger:
        logger.event("node_start", node="planner", chapter_index=chapter_index)

    llm = state.get("llm")  # 由 main/workflow 注入（可选）
    if llm:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
        except Exception as e:  # pragma: no cover
            if state.get("force_llm", False):
                raise RuntimeError(
                    "已指定 LLM 模式，但无法导入 langchain_core.messages（请检查依赖安装/解释器环境）"
                ) from e
            llm = None
    # 1) 如果像“点子包”，先抽取结构化字段并回填 state（但尊重 CLI/config 已提供的覆盖）
    if raw_for_intake and _looks_like_idea_pack(raw_for_intake):
        intake: Dict[str, Any] = {}
        if llm:
            try:
                system0 = SystemMessage(
                    content=(
                        "你是故事策划助手。请从“点子包文件”中抽取可用于写作与工作流的关键信息。\n"
                        "你必须且仅输出一个严格 JSON 对象（不要解释、不要 markdown、不要多余字符）。\n"
                        "严格格式要求（必须遵守，否则视为失败）：\n"
                        "- 只输出一个 JSON object，以 { 开始，以 } 结束\n"
                        "- 只允许以下 4 个 key：project_name / idea / style_override / paragraph_rules\n"
                        "- 不要输出注释、不要输出代码块标记```、不要输出多余字段\n"
                        "JSON schema：\n"
                        "{\n"
                        '  "project_name": "string",\n'
                        '  "idea": "string",\n'
                        '  "style_override": "string",\n'
                        '  "paragraph_rules": "string"\n'
                        "}\n"
                        "要求：\n"
                        "- project_name：短、干净、无换行；不要加书名号/引号；不超过 20 个汉字。\n"
                        "- idea：只保留“故事点子/梗概”（可多段）；不要把“文风/段落规则/参数说明”混进去。\n"
                        "- style_override：只写“文风/视角/节奏/语气”等规则化约束；不要写剧情。\n"
                        "- paragraph_rules：只写段落结构规则（可用换行与短 bullet）；不要写剧情。\n"
                        "- 若未提供某字段，返回空字符串。\n"
                    )
                )
                human0 = HumanMessage(
                    content=(
                        "点子包文件内容：\n"
                        f"{truncate_text(raw_for_intake, max_chars=12000)}\n"
                    )
                )
                schema_text0 = (
                    "{\n"
                    '  "project_name": "string",\n'
                    '  "idea": "string",\n'
                    '  "style_override": "string",\n'
                    '  "paragraph_rules": "string"\n'
                    "}\n"
                )
                def _validate_intake(obj: Dict[str, Any]) -> str:
                    # 只允许这 4 个 key
                    allowed = {"project_name", "idea", "style_override", "paragraph_rules"}
                    for k in obj.keys():
                        if k not in allowed:
                            return f"unexpected_key:{k}"
                    return ""

                if logger:
                    with logger.llm_call(node="planner_intake", chapter_index=chapter_index, messages=[system0, human0]):
                        intake, _raw0, _fr0, _usage0 = invoke_json_with_repair(
                            llm=llm,
                            messages=[system0, human0],
                            schema_text=schema_text0,
                            node="planner_intake",
                            chapter_index=int(chapter_index or 0),
                            logger=logger,
                            max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                            base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                            validate=_validate_intake,
                        )
                else:
                    intake, _raw0, _fr0, _usage0 = invoke_json_with_repair(
                        llm=llm,
                        messages=[system0, human0],
                        schema_text=schema_text0,
                        node="planner_intake",
                        chapter_index=int(chapter_index or 0),
                        logger=None,
                        max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                        base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                        validate=_validate_intake,
                    )
            except Exception:
                intake = {}
        if not intake:
            intake = _parse_idea_pack_fallback(raw_for_intake)

        # 回填 state（CLI/config 优先：若已提供则不覆盖）
        state["idea_intake"] = dict(intake) if isinstance(intake, dict) else {}
        if not str(state.get("project_name_hint", "") or "").strip():
            pn = str(intake.get("project_name", "") or "").strip()
            if pn:
                state["project_name_hint"] = pn
        # user_input：用抽取后的 idea（如果非空）
        extracted_idea = str(intake.get("idea", "") or "").strip()
        if extracted_idea:
            state["user_input"] = extracted_idea
            idea = extracted_idea
        # style/paragraph：只有当外部没给时才用文件里的
        if not str(state.get("style_override", "") or "").strip():
            so = str(intake.get("style_override", "") or "").strip()
            if so:
                state["style_override"] = so
        if not str(state.get("paragraph_rules", "") or "").strip():
            pr = str(intake.get("paragraph_rules", "") or "").strip()
            if pr:
                state["paragraph_rules"] = pr

    # 兜底构造（用于 LLM 解析失败/调用异常时不崩）
    def _template_planner() -> Dict[str, Any]:
        default_instr = {
            "世界观设定": "请基于核心点子，构建故事发生的世界背景、基本规则与核心冲突（用于 Canon）。",
            "核心角色": "请设计主要角色的人物卡，包括性格、动机、背景、能力、禁忌与关系网（用于 Canon）。",
            "主线脉络": "请规划故事的整体主线发展，以及前期的关键剧情节点与节奏安排。",
            "开篇基调": "请确定小说的整体开篇风格与情绪基调，例如轻松、热血或悬疑，并给出可执行的文风约束。",
        }
        name_hint = str(state.get("project_name_hint", "") or "").strip()
        idea_text = str(state.get("user_input", "") or idea or "").strip()
        return {
            "项目名称": name_hint or idea_text,
            "任务列表": [
                {
                    "任务名称": t["task_name"],
                    "执行者": t["executor"],
                    "任务指令": (t.get("hint") or "").strip() or default_instr.get(t["task_name"], f"请完成“{t['task_name']}”相关产出。"),
                }
                for t in tasks
            ],
        }

    if llm:
        tasks_lines = []
        for t in tasks:
            tasks_lines.append(
                f'    {{"任务名称":"{t["task_name"]}","执行者":"{t["executor"]}","任务指令":"string"}}'
            )
        schema_tasks = ",\n".join(tasks_lines)
        allowed_executors = sorted({t["executor"] for t in tasks})
        system = SystemMessage(
            content=(
                "你是资深故事策划“玲珑”，负责分析用户点子并拆解任务。\n"
                "你必须根据“当前启用的 agent/任务槽位”来拆解任务：任务名称与执行者必须与给定槽位完全一致。\n"
                "你必须且仅输出一个格式严格的 JSON 对象（不要多任何解释、不要 markdown、不要多余字符）。\n"
                "严格格式要求（必须遵守，否则视为失败）：\n"
                "- 只输出一个 JSON object，以 { 开始，以 } 结束\n"
                "- 不要输出注释、不要输出代码块标记```、不要输出多余字段\n"
                f"可用执行者（只能从中选择）：{allowed_executors}\n"
                "输出 JSON schema：\n"
                '{\n'
                '  "项目名称": "string",\n'
                '  "任务列表": [\n'
                f"{schema_tasks}\n"
                "  ]\n"
                "}\n"
                "约束：\n"
                "- 项目名称：短、干净、无换行；不要加书名号/引号；不超过 20 个汉字。\n"
                "- 任务列表数量必须与槽位数量一致，顺序保持与 schema 一致。\n"
                "- 每条任务指令必须“可执行”，包含输出要求与边界（避免泛泛而谈）。\n"
                "- 命名纪律（为了 150 章长跑一致性）：除非上游明确提供，否则避免创造大量新专有名词（门派/功法/地名等）；需要新概念时，用“模糊描述+可落地约束”，不要给新名字。\n"
                "- 文风/段落：如果提供了“文风覆盖/段落规则”，请在相应任务指令中显式引用为硬约束。\n"
                "- 目标：降低返工与设定漂移，宁可少而准。\n"
            )
        )
        # 给每个槽位提供 hint（如果有）
        hints = "\n".join(
            [f'- {t["task_name"]}（{t["executor"]}）：{t.get("hint","")}' for t in tasks if str(t.get("hint","") or "").strip()]
        ).strip()
        human = HumanMessage(
            content=(
                f"用户输入（点子/梗概）：\n{idea}\n\n"
                + (
                    ("建议项目名（来自点子包/外部指定）：\n" + str(state.get("project_name_hint", "") or "").strip() + "\n\n")
                    if str(state.get("project_name_hint", "") or "").strip()
                    else ""
                )
                + (
                    ("文风覆盖（注入写手/主编）：\n" + truncate_text(str(state.get("style_override", "") or ""), max_chars=1200) + "\n\n")
                    if str(state.get("style_override", "") or "").strip()
                    else ""
                )
                + (
                    ("段落/结构规则：\n" + truncate_text(str(state.get("paragraph_rules", "") or ""), max_chars=800) + "\n\n")
                    if str(state.get("paragraph_rules", "") or "").strip()
                    else ""
                )
                + ("任务槽位提示：\n" + hints + "\n" if hints else "")
            )
        )
        schema_text_main = (
            "{\n"
            '  "项目名称": "string",\n'
            '  "任务列表": [\n'
            "    {\"任务名称\":\"string\",\"执行者\":\"string\",\"任务指令\":\"string\"}\n"
            "  ]\n"
            "}\n"
        )
        slot_names = [str(t.get("task_name", "") or "") for t in tasks]
        slot_execs = [str(t.get("executor", "") or "") for t in tasks]

        def _validate_planner(obj: Dict[str, Any]) -> str:
            if "项目名称" not in obj:
                return "missing:项目名称"
            arr = obj.get("任务列表")
            if not isinstance(arr, list) or len(arr) != len(tasks):
                return f"任务列表长度不匹配(expected={len(tasks)})"
            for i, it in enumerate(arr):
                if not isinstance(it, dict):
                    return f"任务列表[{i}]不是object"
                if str(it.get("任务名称", "") or "") != slot_names[i]:
                    return f"任务名称不匹配(idx={i})"
                if str(it.get("执行者", "") or "") != slot_execs[i]:
                    return f"执行者不匹配(idx={i})"
                if not str(it.get("任务指令", "") or "").strip():
                    return f"任务指令为空(idx={i})"
            return ""

        if logger:
            cfg = getattr(getattr(llm, "client", None), "base_url", None)
            model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            with logger.llm_call(
                node="planner",
                chapter_index=chapter_index,
                messages=[system, human],
                model=model,
                base_url=str(getattr(llm, "base_url", "") or cfg or ""),
            ):
                planner_result, _rawm, _frm, _usm = invoke_json_with_repair(
                    llm=llm,
                    messages=[system, human],
                    schema_text=schema_text_main,
                    node="planner",
                    chapter_index=chapter_index,
                    logger=logger,
                    max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                    base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                    validate=_validate_planner,
                )
        else:
            planner_result, _rawm, _frm, _usm = invoke_json_with_repair(
                llm=llm,
                messages=[system, human],
                schema_text=schema_text_main,
                node="planner",
                chapter_index=chapter_index,
                logger=None,
                max_attempts=int(state.get("llm_max_attempts", 3) or 3),
                base_sleep_s=float(state.get("llm_retry_base_sleep_s", 1.0) or 1.0),
                validate=_validate_planner,
            )

        if not planner_result:
            if logger:
                logger.event("llm_parse_failed", node="planner", chapter_index=chapter_index, action="fallback_template")
            planner_result = _template_planner()

        state["planner_result"] = planner_result
        state["planner_json"] = json.dumps(planner_result, ensure_ascii=False, indent=2)
        state["planner_used_llm"] = True
        if logger:
            logger.event(
                "node_end",
                node="planner",
                chapter_index=chapter_index,
                used_llm=True,
                planner_json_chars=len(state.get("planner_json", "") or ""),
            )
        return state

    # 模板模式（无 LLM）
    planner_result = _template_planner()
    state["planner_result"] = planner_result
    state["planner_json"] = json.dumps(planner_result, ensure_ascii=False, indent=2)
    state["planner_used_llm"] = False
    if logger:
        logger.event(
            "node_end",
            node="planner",
            chapter_index=chapter_index,
            used_llm=False,
            planner_json_chars=len(state.get("planner_json", "") or ""),
        )

    return state

