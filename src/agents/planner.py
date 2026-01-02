from state import StoryState

def planner_agent(state: StoryState) -> StoryState:
    """
    策划 Agent（模板版，不接 LLM）
    输出 dict，兼容 Writer Agent
    """
    idea = state.get("user_input", "默认点子")

    # 构建 Planner 输出
    state["planner_result"] = {
        "项目名称": f"《{idea.strip()}》",
        "任务列表": [
            {
                "任务名称": "世界观设定",
                "执行者": "架构师",
                "任务指令": "请基于核心点子，构建故事发生的世界背景、基本规则与核心冲突。"
            },
            {
                "任务名称": "核心角色",
                "执行者": "角色导演",
                "任务指令": "请设计主要角色的人物卡，包括性格、动机、背景与成长方向。"
            },
            {
                "任务名称": "主线脉络",
                "执行者": "编剧",
                "任务指令": "请规划故事的整体主线发展，以及前期的关键剧情节点。"
            },
            {
                "任务名称": "开篇基调",
                "执行者": "策划",
                "任务指令": "请确定小说的整体开篇风格与情绪基调，例如轻松、热血或悬疑。"
            },
        ],
    }

    return state

