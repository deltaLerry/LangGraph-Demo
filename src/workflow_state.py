from dataclasses import dataclass
from typing import Optional

from models.planner_models import PlannerResult


@dataclass
class WorkflowState:
    """
    LangGraph 工作流的全局状态
    """
    idea: str
    planner_result: Optional[PlannerResult] = None

