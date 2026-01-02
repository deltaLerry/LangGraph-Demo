from agents.planner import PlannerAgent
from workflow_state import WorkflowState


def planner_node(state: WorkflowState) -> dict:
    """
    LangGraph Node：策划
    """
    planner = PlannerAgent()
    result = planner.plan(state.idea)

    return {
        "planner_result": result
    }

