from workflow import build_workflow
from workflow_state import WorkflowState


def main():
    idea = input("请输入你的小说点子：\n> ")

    workflow = build_workflow()

    final_state = workflow.invoke(
        WorkflowState(idea=idea)
    )

    print("\n=== LangGraph 执行结果 ===")
    print(final_state["planner_result"])


if __name__ == "__main__":
    main()

