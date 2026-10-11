# 代码生成 Agent

基于通义千问（OpenAI 兼容接口）的命令行代码生成 Agent。核心特点是**工具调用反馈闭环**：LLM 生成的代码不是直接交给用户，而是先在沙盒中实际运行验证，出错则由 LLM 根据报错自动修正后重试，验证通过才返回最终答案。

## 功能

1. **完整 Agent 循环**：自然语言需求 → LLM 推理 → 调用代码执行工具 → 拿到输出/报错 → LLM 修正 → 返回结果
2. **简易沙盒**：独立子进程运行 Python，捕获 stdout / traceback，超时强杀
3. **上下文记忆**：多轮对话历史完整保留，支持连续多轮需求
4. **错误处理**：API 网络异常指数退避重试、Key 错误明确提示、代码异常回填 LLM 修正
5. **纯命令行交互**：循环输入需求，输入 `exit` 退出

## 项目结构

```
agent/
├─ main.py           # 入口：加载配置、命令行交互循环
├─ agent.py          # Agent 核心循环（推理→工具→反馈闭环、上下文记忆）
├─ llm_client.py     # 大模型 API 封装（原生 requests，异常捕获+重试）
├─ tools.py          # 代码执行沙盒（子进程、捕获输出、超时保护）
├─ .env              # API Key 配置（勿提交，已有 .gitignore 排除）
├─ .env.example      # 配置模板
├─ requirements.txt  # 依赖（仅 requests + python-dotenv）
├─ README.md         # 本文件
└─ Design.md         # 设计文档（对应作业评审）
```

## 运行步骤

### 1. 获取 API Key

访问 [百炼控制台](https://bailian.console.aliyun.com)，开通服务后在「API-KEY」页面创建 Key。

### 2. 填写配置

打开 `.env`，将 Key 填入：

```
LLM_API_KEY=sk-你的真实Key
```

### 3. 安装依赖并运行

```bash
cd D:\agent
pip install -r requirements.txt
python main.py
```

### 4. 示例会话

```
你> 写一个函数判断素数，并测试 1~20 中所有的素数
  [Agent] 第1步: 执行代码沙盒... 失败(进程异常退出，退出码 1)
  [Agent] 第2步: 执行代码沙盒... 成功

Agent> 思路：先用试除法判断素数……
```python
def is_prime(n): ...
```
1~20 中的素数为：2 3 5 7 11 13 17 19

你> 把刚才的函数改成统计 100 以内的素数个数
  （Agent 记得上一轮的代码，直接基于历史修改并验证）

你> exit
```

## 退出方式

输入 `exit`、`quit` 或 `退出`；或按 `Ctrl+C`。

详细架构设计见 [Design.md](Design.md)。
