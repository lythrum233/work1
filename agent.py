"""agent.py —— Agent 核心循环(本项目的架构核心)

实现"生成 → 执行 → 反馈 → 自动修复"完整闭环：
    用户需求
      → LLM 生成代码 → 沙盒执行
      → 执行成功：退出修复循环，整理结果输出给用户
      → 执行失败：完整报错(stdout/异常堆栈/失败标记)回填上下文记忆
              → LLM 分析报错、修改代码 → 再次调用代码执行工具
              → 最多 MAX_REPAIR_ROUNDS(2) 轮修复；
                超限仍失败则终止迭代，汇总各版本尝试与报错告知用户

上下文记忆：self.messages 保存完整多轮历史(用户提问、生成的代码、
工具完整返回、assistant 回复)，跨用户请求持续保留。
"""

import json

from llm_client import LLMClient
from tools import execute_python_code, TOOL_SCHEMAS

SYSTEM_PROMPT = """你是一个代码生成 Agent。你的工作流程：
1. 收到用户的自然语言需求后，先思考如何用 Python 解决；
2. 必须调用 execute_python_code 工具实际运行你写的代码进行验证，不允许只给出代码而不运行；
3. 仔细观察运行结果(stdout / traceback / 超时信息)。

如果代码执行工具返回执行失败、异常堆栈：
1. 仔细分析报错原因，修改代码尝试修复该问题；
2. 修改完成后必须再次调用代码执行工具运行修复后的代码；
3. 最多允许总共2轮修复尝试；两轮之后依旧报错，则停止迭代，向用户说明报错信息与已经尝试过的方案；
严禁拿到报错就直接打印错误信息结束整个任务。

代码验证通过后，向用户输出最终答案：先简要说明思路，再给出完整代码(markdown 代码块)，以及运行结果要点。
注意：
- 工具的 code 参数必须是一个完整、可直接运行的 Python 脚本(包含 import)；
- 不要假设有外部文件，如需数据请在代码中直接构造；
- 禁止执行任何破坏性操作(删文件、危险网络请求等)。"""

# ★[改动1] 新增：最大自动修复轮次(执行失败后允许 LLM 修复并重跑的次数)
MAX_REPAIR_ROUNDS = 2
# 总推理步数硬上限(初始执行 + 修复轮次 + 兜底)，防止死循环
MAX_STEPS = 10
# 历史消息条数上限，超过后保留 system + 最近消息，防止 token 膨胀
MAX_HISTORY = 60


class Agent:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        # 上下文记忆：完整对话历史(含工具调用与执行结果)，跨轮次保留
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def run(self, user_input: str) -> str:
        """处理一次用户输入，返回 Agent 的最终回答文本。

        ★[改动2] 内部为"生成 → 执行 → 失败自动修复(最多2轮)"迭代循环：
        执行失败时任务不直接结束，而是带着完整报错回到 LLM 修正重跑。
        """
        # 上下文记忆：用户原始提问追加到 messages
        self.messages.append({"role": "user", "content": user_input})

        repair_rounds = 0   # ★[改动] 已消耗的修复轮次
        attempts = []       # ★[改动] 记录每次执行的代码与完整结果，用于最终失败汇总

        try:
            for step in range(1, MAX_STEPS + 1):
                # ---- 1. LLM 推理 ----
                resp = self.llm.chat(self.messages, tools=TOOL_SCHEMAS)

                # ---- 2. 无工具调用 → 验证已通过(或无需执行)，输出最终答案 ----
                if not resp["tool_calls"]:
                    self.messages.append({"role": "assistant", "content": resp["content"]})
                    self._trim_history()
                    return resp["content"]

                # ---- 3. 记录 assistant 的工具调用意图(其中含生成的代码) ----
                self.messages.append({
                    "role": "assistant",
                    "content": resp["content"],
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(
                                    tc["arguments"], ensure_ascii=False
                                ),
                            },
                        }
                        for tc in resp["tool_calls"]
                    ],
                })

                # ---- 4. 执行工具，完整结果回填上下文 ----
                round_failed = False   # 本轮是否有执行失败
                for tc in resp["tool_calls"]:
                    if tc["name"] == "execute_python_code":
                        code = tc["arguments"].get("code", "")
                        result = execute_python_code(code)
                        attempts.append({"code": code, "result": result})
                        if result["success"]:
                            print(f"  [Agent] 第{step}步: 代码执行成功")
                        else:
                            round_failed = True
                            # 打印报错最后一行(完整堆栈已回填 messages 给 LLM)
                            last_line = (result["error_msg"] or "未知错误").strip().splitlines()[-1]
                            print(f"  [Agent] 第{step}步: 代码执行失败 [{last_line[:100]}]")
                    else:
                        result = {"success": False, "stdout": "", "stderr": "",
                                  "error_msg": f"未知工具: {tc['name']}"}
                        round_failed = True

                    # ★[改动3] 工具完整返回(stdout/完整异常堆栈 error_msg/失败标记)
                    # 全部追加到上下文记忆 messages，供 LLM 分析修复
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                # ---- 5. ★[改动4] 修复迭代控制 ----
                if round_failed:
                    if repair_rounds >= MAX_REPAIR_ROUNDS:
                        # 已用完 2 轮修复仍失败 → 终止迭代，汇总告知用户
                        print(f"  [Agent] 已达最大修复轮次({MAX_REPAIR_ROUNDS})且仍失败，终止迭代并汇总")
                        summary = self._build_failure_summary(attempts)
                        self.messages.append({"role": "assistant", "content": summary})
                        self._trim_history()
                        return summary
                    repair_rounds += 1
                    print(f"  [Agent] 进入第 {repair_rounds}/{MAX_REPAIR_ROUNDS} 轮自动修复(报错已回填，交由模型修正)...")
                # 无论成败，continue 都把工具结果交回 LLM 推理
                # (成功 → LLM 整理最终答案；失败 → LLM 按报错修复代码)

            # 总步数兜底(理论上到不了：2轮修复上限远小于 MAX_STEPS)
            print("  [Agent] 已达最大推理步数，请求模型总结当前进展...")
            resp = self.llm.chat(self.messages)  # 不带工具
            self.messages.append({"role": "assistant", "content": resp["content"]})
            self._trim_history()
            return resp["content"]

        except Exception:
            # 请求中途失败时，把"悬空"的 user 消息移除，保持历史一致性
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            raise

    @staticmethod
    def _build_failure_summary(attempts: list) -> str:
        """★[改动5] 修复超限后的失败汇总。

        列出已经尝试过的各版本代码与完整报错，明确告知用户无法自动修复。
        本地生成(不额外调用 API)，保证输出确定、可控。
        """
        failed = [a for a in attempts if not a["result"]["success"]]
        repair_used = max(0, len(failed) - 1)
        lines = [
            f"很抱歉，代码经过初始执行 + {repair_used} 轮自动修复后仍然失败，"
            f"已达最大修复轮次({MAX_REPAIR_ROUNDS})，本次任务无法自动完成。",
            "以下是历次尝试的代码与报错，供你排查：",
        ]
        for i, a in enumerate(failed, 1):
            code = a["code"].strip() or "(空代码)"
            error = a["result"]["error_msg"] or a["result"]["stderr"] or "未知错误"
            if len(error) > 800:  # 防止超长堆栈刷屏
                error = error[:800] + "\n……(报错过长，已截断)"
            lines.append(f"\n【尝试 {i}】代码:\n```python\n{code}\n```")
            lines.append(f"报错:\n{error}")
        lines.append("\n建议：根据上述报错人工检查逻辑，或换一种实现思路后再试。")
        return "\n".join(lines)

    def _trim_history(self):
        """上下文记忆裁剪：保留 system 提示 + 最近 MAX_HISTORY 条消息。"""
        if len(self.messages) > MAX_HISTORY:
            system = self.messages[0]
            recent = self.messages[-MAX_HISTORY:]
            self.messages = [system] + recent
