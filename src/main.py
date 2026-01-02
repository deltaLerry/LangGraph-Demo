from agents.planner import PlannerAgent


def main():
    idea = input("请输入你的小说点子：\n> ")

    planner = PlannerAgent()
    result = planner.plan(idea)

    print("\n=== 策划输出 ===")
    print(result)


if __name__ == "__main__":
    main()

