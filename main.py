"""main.py —— 程序入口：命令行交互循环

启动流程：加载 .env → 初始化 LLM 客户端与 Agent → 进入交互循环。
循环输入用户需求，输入 exit / quit / 退出 时结束程序。
"""

import os
import sys

from dotenv import load_dotenv

from agent import Agent
from llm_client import LLMClient, APIKeyError

# 通义千问(百炼)的 OpenAI 兼容模式地址，可通过 .env 覆盖
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


def main():
    # Windows 控制台中文防乱码
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # ---- 1. 读取配置 ----
    load_dotenv()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)

    print("=" * 60)
    print("  代码生成 Agent (通义千问 + Python 代码沙盒)")
    print("=" * 60)

    if not api_key or api_key.startswith("sk-你的") or "填入" in api_key:
        print("\n[错误] 尚未配置 API Key！")
        print("请打开 D:\\agent\\.env，将 LLM_API_KEY 填写为你的通义千问 API Key")
        print("获取地址: https://bailian.console.aliyun.com (百炼控制台 -> API-KEY)")
        sys.exit(1)

    # ---- 2. 初始化 Agent ----
    llm = LLMClient(api_key=api_key, base_url=base_url, model=model)
    agent = Agent(llm)
    print(f"模型: {model} | 接口: {base_url}")
    print("输入你的自然语言需求(多轮对话，保留上下文)，输入 exit 退出。\n")

    # ---- 3. 命令行交互循环 ----
    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "退出"):
            print("再见！")
            break

        try:
            answer = agent.run(user_input)
            print(f"\nAgent> {answer}\n")
        except APIKeyError as e:
            # Key 错误：继续对话没有意义，提示修正后重启
            print(f"\n[Key错误] {e}")
            print("请修正 .env 中的 LLM_API_KEY 后重新运行程序。")
            break
        except (ConnectionError, Exception) as e:
            # 网络重试仍失败或其他异常：提示但不退出，可继续输入
            print(f"\n[请求失败] {type(e).__name__}: {e}")
            print("本轮处理失败，可直接重试上一条需求。")


if __name__ == "__main__":
    main()
