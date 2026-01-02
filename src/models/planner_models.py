from dataclasses import dataclass
from typing import List


@dataclass
class Task:
    task_name: str
    executor: str
    instruction: str


@dataclass
class PlannerResult:
    project_name: str
    tasks: List[Task]

