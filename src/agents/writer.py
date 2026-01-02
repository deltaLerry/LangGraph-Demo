from state import StoryState

def writer_agent(state: StoryState) -> StoryState:
    """
    写手 Agent（模板版）
    根据 planner_result 输出示例正文
    """
    planner_result = state.get("planner_result")
    if not planner_result:
        raise ValueError("writer_agent: planner_result is missing")

    project_name = planner_result.get("项目名称", "未命名项目")

    # 生成示例正文
    content = f"""
《{project_name}》

这是根据策划结果生成的第一段示例正文。
当前阶段仅用于验证 Planner → Writer 的工作流。
"""
    state["writer_result"] = content.strip()
    return state

