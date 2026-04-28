import sys
from agent.core import run_agent, AgentSession


def main():
    print("=== AI 本地路线规划 Agent ===")
    print("输入你的出行需求，Agent 会为你规划路线。")
    print("之后可以继续提要求修改（换个餐厅、加个景点等）")
    print("输入 'quit' 退出。\n")

    session = AgentSession()

    default_city = input("请设置默认城市（直接回车跳过，后续可单独指定）: ").strip()
    if default_city:
        session.default_city = default_city
        print(f"已设置默认城市：{default_city}\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            sys.exit(0)

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "退出"):
            print("再见！")
            break

        print("\n--- 规划中 ---")
        result, session = run_agent(user_input, session)
        print(f"\n--- 路线方案 ---\n{result}\n")
        print("-" * 60 + "\n")


if __name__ == "__main__":
    main()
