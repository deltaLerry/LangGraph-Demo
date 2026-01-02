from langgraph.graph import StateGraph, END

from workflow_state import WorkflowState
from workflow_nodes import planner_node


def build_workflow():
    graph = StateGraph(WorkflowState)

    # 注册节点
    graph.add_node("planner", planner_node)

    # 定义入口
    graph.set_entry_point("planner")

    # 定义结束
    graph.add_edge("planner", END)

    return graph.compile()

