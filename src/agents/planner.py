from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from state import StoryState
from debug_log import truncate_text


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
        system = SystemMessage(
            content=(
                "你是资深故事策划“玲珑”，负责分析用户点子并拆解任务。\n"
                "你必须且仅输出一个格式严格的 JSON 对象（不要多任何解释、不要 markdown）。\n"
                "输出 JSON schema：\n"
                '{\n'
                '  "项目名称": "string",\n'
                '  "任务列表": [\n'
                '    {"任务名称":"世界观设定","执行者":"架构师","任务指令":"string"},\n'
                '    {"任务名称":"核心角色","执行者":"角色导演","任务指令":"string"},\n'
                '    {"任务名称":"主线脉络","执行者":"编剧","任务指令":"string"},\n'
                '    {"任务名称":"开篇基调","执行者":"策划","任务指令":"string"}\n'
                "  ]\n"
                "}\n"
            )
        )
        human = HumanMessage(content=f"用户输入：\n{idea}")
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
    planner_result = {
        "项目名称": f"《{idea.strip()}》",
        "任务列表": [
            {
                "任务名称": "世界观设定",
                "执行者": "架构师",
                "任务指令": "请基于核心点子，构建故事发生的世界背景、基本规则与核心冲突。",
            },
            {
                "任务名称": "核心角色",
                "执行者": "角色导演",
                "任务指令": "请设计主要角色的人物卡，包括性格、动机、背景与成长方向。",
            },
            {
                "任务名称": "主线脉络",
                "执行者": "编剧",
                "任务指令": "请规划故事的整体主线发展，以及前期的关键剧情节点。",
            },
            {
                "任务名称": "开篇基调",
                "执行者": "策划",
                "任务指令": "请确定小说的整体开篇风格与情绪基调，例如轻松、热血或悬疑。",
            },
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

