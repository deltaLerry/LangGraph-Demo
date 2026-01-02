from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, List

from state import StoryState
from debug_log import truncate_text
from llm_meta import extract_finish_reason_and_usage


def _extract_first_json_obj(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    # 先尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        pass

    # 再尝试抽取第一个 { ... } 片段
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("planner_agent: 无法从 LLM 输出中提取 JSON")
    return json.loads(m.group(0))


def planner_agent(state: StoryState) -> StoryState:
    """
    策划 Agent：
    - 无 LLM：使用模板输出（可跑通工作流）
    - 有 LLM：严格要求只输出 JSON，并解析为 dict
    """
    idea = state.get("user_input", "默认点子")
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
                "你必须且仅输出一个格式严格的 JSON 对象（不要多任何解释、不要 markdown）。\n"
                f"可用执行者（只能从中选择）：{allowed_executors}\n"
                "输出 JSON schema：\n"
                '{\n'
                '  "项目名称": "string",\n'
                '  "任务列表": [\n'
                f"{schema_tasks}\n"
                "  ]\n"
                "}\n"
                "约束：\n"
                "- 任务列表数量必须与槽位数量一致，顺序保持与 schema 一致。\n"
                "- 每条任务指令必须“可执行”，包含输出要求与边界（避免泛泛而谈）。\n"
            )
        )
        # 给每个槽位提供 hint（如果有）
        hints = "\n".join(
            [f'- {t["task_name"]}（{t["executor"]}）：{t.get("hint","")}' for t in tasks if str(t.get("hint","") or "").strip()]
        ).strip()
        human = HumanMessage(
            content=(
                f"用户输入：\n{idea}\n\n"
                + ("任务槽位提示：\n" + hints + "\n" if hints else "")
            )
        )
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
                resp = llm.invoke([system, human])
        else:
            resp = llm.invoke([system, human])
        if logger:
            logger.event(
                "llm_response",
                node="planner",
                chapter_index=chapter_index,
                content=truncate_text(str(getattr(resp, "content", "") or ""), max_chars=getattr(logger, "max_chars", 20000)),
                finish_reason=extract_finish_reason_and_usage(resp)[0],
                token_usage=extract_finish_reason_and_usage(resp)[1],
            )
        planner_result = _extract_first_json_obj(getattr(resp, "content", "") or "")
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
    default_instr = {
        "世界观设定": "请基于核心点子，构建故事发生的世界背景、基本规则与核心冲突（用于 Canon）。",
        "核心角色": "请设计主要角色的人物卡，包括性格、动机、背景、能力、禁忌与关系网（用于 Canon）。",
        "主线脉络": "请规划故事的整体主线发展，以及前期的关键剧情节点与节奏安排。",
        "开篇基调": "请确定小说的整体开篇风格与情绪基调，例如轻松、热血或悬疑，并给出可执行的文风约束。",
    }
    planner_result = {
        # 注意：项目名将用于 projects/<project> 目录名；避免额外包裹符号导致同名项目出现两个目录
        "项目名称": f"{idea.strip()}",
        "任务列表": [
            {
                "任务名称": t["task_name"],
                "执行者": t["executor"],
                "任务指令": (t.get("hint") or "").strip() or default_instr.get(t["task_name"], f"请完成“{t['task_name']}”相关产出。"),
            }
            for t in tasks
        ],
    }
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

