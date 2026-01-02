from models.planner_models import PlannerResult, Task


class PlannerAgent:
    """
    策划 Agent（玲珑）
    当前为规则 / 模板版本，不接 LLM
    """

    def plan(self, idea: str) -> PlannerResult:
        project_name = self._generate_project_name(idea)

        tasks = [
            Task(
                task_name="世界观设定",
                executor="架构师",
                instruction="请基于核心点子，构建故事发生的世界背景、基本规则与核心冲突。"
            ),
            Task(
                task_name="核心角色",
                executor="角色导演",
                instruction="请设计主要角色的人物卡，包括性格、动机、背景与成长方向。"
            ),
            Task(
                task_name="主线脉络",
                executor="编剧",
                instruction="请规划故事的整体主线发展，以及前期的关键剧情节点。"
            ),
            Task(
                task_name="开篇基调",
                executor="策划",
                instruction="请确定小说的整体开篇风格与情绪基调，例如轻松、热血或悬疑。"
            ),
        ]

        return PlannerResult(
            project_name=project_name,
            tasks=tasks
        )

    def _generate_project_name(self, idea: str) -> str:
        # MVP 阶段：简单规则，后面交给 LLM
        return f"《{idea.strip()}》"

