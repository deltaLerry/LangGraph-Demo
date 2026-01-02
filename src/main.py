from langgraph.graph import StateGraph, END
from state import StoryState
from agents.planner import planner_agent
from agents.writer import writer_agent

def main():
    # 创建 LangGraph 图
    graph = StateGraph(StoryState)

    # 添加节点
    graph.add_node("planner", planner_agent)
    graph.add_node("writer", writer_agent)

    # 设置入口
    graph.set_entry_point("planner")

    # 设置顺序：Planner -> Writer -> END
    graph.add_edge("planner", "writer")
    graph.add_edge("writer", END)

    # 编译图
    app = graph.compile()

    # 初始化 state
    initial_state: StoryState = {
        "user_input": "一个普通人意外进入修仙世界，被迫卷入宗门纷争"
    }

    # 执行 workflow
    final_state = app.invoke(initial_state)

    # 输出结果
    print("=== Planner Result ===")
    print(final_state["planner_result"])

    print("\n=== Writer Result ===")
    print(final_state["writer_result"])

if __name__ == "__main__":
    main()

