# 从零构建 Coding Agent：原理拆解与代码实战

> 本文档以五个代表性 Coding Agent（Claude Code / Codex / Hermes / OpenClaw / ADK-Python）的架构思想为参照，
> 结合本项目 `agents/` 下的**可运行代码**，逐维度拆解 Agent 的核心设计。
>
> 每一节都遵循同一结构：**业界怎么做 → 我们怎么实现 → 设计取舍**。

---

## 目录

1. [Coding Agent 全景图：为什么值得拆](#一coding-agent-全景图)
2. [Agent Loop：思考-行动-观察的循环](#二agent-loop思考-行动-观察的循环)
3. [工具系统：注册、Schema、调度](#三工具系统注册schema调度)
4. [上下文管理：分级递进压缩](#四上下文管理分级递进压缩)
5. [流式输出：边想边说的体验](#五流式输出边想边说的体验)
6. [Planning：todo.md 任务计划](#六planningtodomd-任务计划)
7. [Memory：跨会话的持久记忆](#七memory跨会话的持久记忆)
8. [Eval：自动化评测体系](#八eval自动化评测体系)
9. [工程化：目录结构与依赖注入](#九工程化目录结构与依赖注入)
10. [对照总结与进阶方向](#十对照总结与进阶方向)

---

## 一、Coding Agent 全景图

所有 Coding Agent 本质上都在做同一件事：

```
while (true) { 思考 → 行动 → 观察 }
```

但为什么有的产品一句话就能完成复杂重构，有的却在简单任务上反复犯错？

答案不在模型能力（大家用的是同一批模型），而在**架构设计**——尤其是以下四个维度：

| 维度                      | 决定了什么               | 本项目对应模块         |
| ------------------------- | ------------------------ | ---------------------- |
| **Agent Loop 设计** | 模型如何持续思考和行动   | `agent/core.py`      |
| **上下文管理**      | 模型能"看到"多少有效信息 | `context/manager.py` |
| **工具调度**        | 模型能"做"什么、做得多快 | `tools/registry.py`  |
| **错误恢复**        | 出错后能否自愈而非崩溃   | `core.py` 的重试逻辑 |

本项目最终的目录结构如下，后文会逐一展开：

```
agents/
├── agent/
│   ├── core.py              # Agent Loop 主循环（纯函数 + 流式）
│   └── planner.py           # Planning 模块（todo.md 模式）
├── tools/
│   ├── registry.py          # 工具注册中心（自动 Schema 生成）
│   └── builtin/             # 内置工具（文件读写、搜索）
├── context/
│   └── manager.py           # 上下文窗口管理（分级压缩）
├── memory/
│   └── long_term.py         # 持久记忆（文件系统）
├── eval/
│   ├── runner.py            # 评测运行器
│   ├── judge.py             # 自动评判
│   └── cases/               # 黄金测试用例（YAML）
├── main.py                  # 入口
└── README.md
```

---

## 二、Agent Loop：思考-行动-观察的循环

### 业界怎么做

**Claude Code** 的核心循环采用 `AsyncGenerator + while(true)` 模式，用一个可变的 `state` 跨迭代追踪转移原因：

```typescript
// Claude Code: src/query.ts（节选）
async function* queryLoop(params): AsyncGenerator<StreamEvent, Terminal> {
let state: State = { messages, turnCount: 1, transition: undefined }
while (true) {
// 分级压缩 → LLM 调用 → 流式工具执行 → 错误恢复
if (!needsFollowUp) break  // 没有工具调用 → 结束
}
}
```

**Codex** 用 Rust 实现 `run_turn`，核心判断是 `needs_follow_up`：模型请求了工具就继续循环，否则结束 Turn。

**Hermes** 引入了 `IterationBudget` 预算制，防止 Agent 无限循环，又用 "Grace Call" 给它最后一次体面收尾的机会。

三者的共性骨架是一致的：**循环调用 LLM → 判断是否需要工具 → 执行工具并把结果喂回 → 直到模型不再请求工具**。

### 我们怎么实现

我们的 Agent Loop 在 `agent/core.py`，采用 Python 的同步**生成器**（`Generator`），逐步 `yield` 事件——这正是 Claude Code "AsyncGenerator 流式透传" 思想的轻量版本：

```python
# agents/agent/core.py
"""
Agent 核心 Loop —— 流式 + 工具调用 + Planning 集成
纯函数设计：所有外部依赖通过参数注入，不依赖模块级全局变量。
"""
import json
import time
from typing import Generator

from tools.registry import get_tool_schemas, execute_tool

def agent_loop_streaming(
messages: list,
client,
ctx_mgr,
plan_mgr,
model: str = "deepseek-v4-flash",
max_steps: int = 20,
) -> Generator[dict, None, None]:
for step in range(max_steps):
# === 上下文压缩（每轮 API 调用前检查）===
before = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
messages = ctx_mgr.manage(messages)
after = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
if before != after:
yield {"type": "thinking",
"content": f"📦 上下文压缩: {before//4:,}t → {after//4:,}t "
f"(缩减 {(1-after/before)*100:.0f}%)"}

yield {"type": "thinking", "content": f"正在思考（第 {step + 1} 步）..."}

# === 流式调用 LLM（带指数退避重试）===
for retry in range(3):
try:
stream = client.chat.completions.create(
model=model,
messages=messages,
tools=get_tool_schemas(),
tool_choice="auto",
stream=True,
)
break
except Exception as e:
if retry == 2:
yield {"type": "done", "content": f"❌ API 调用失败: {e}"}
return
time.sleep(2 ** retry)
# ... 逐 chunk 处理、工具执行（见后文）...
```

**对照 `while(true)` 与 `max_steps`：** 我们用 `for step in range(max_steps)` 替代了无限 `while`，这本质上是 Hermes `IterationBudget` 的极简版——给循环一个硬上限，防止模型陷入死循环烧钱。循环结束时若仍未完成，会 `yield` 一个 `max_steps` 事件：

```python
# agents/agent/core.py（末尾）
# === 达到最大步数 ===
plan_info = ""
if not plan_mgr.is_empty():
plan_info = f" | {plan_mgr.get_progress_summary()}"
yield {"type": "max_steps", "content": f"⚠️ 达到最大步数限制（{max_steps}步）{plan_info}"}
```

### 错误恢复

Claude Code 有 `withhold + 多策略`，Codex 有"沙箱升级重试"。我们的版本虽简单，但抓住了核心——**指数退避重试**（上面 `for retry in range(3)` 那段）：API 抖动时等 `1s → 2s → 4s` 再试，三次失败才放弃。这是生产级 Agent 的基本素养。

### 设计取舍

| 维度     | 业界（Claude Code）                 | 本项目                          |
| -------- | ----------------------------------- | ------------------------------- |
| 循环控制 | `while(true)` + transition 状态机 | `for range(max_steps)` 硬上限 |
| 流式     | AsyncGenerator                      | 同步 Generator                  |
| 工具执行 | 边收边执行 + 智能并发               | 收齐后串行执行                  |
| 错误恢复 | withhold/多策略                     | 指数退避重试 3 次               |

> **核心收获**：Agent Loop 的灵魂是「生成器 + 事件流」。用 `yield` 而非 `return`，
> 调用方就能实时消费每一步进展，这是流式体验的根基。

---

## 三、工具系统：注册、Schema、调度

### 业界怎么做

**ADK-Python** 的哲学是"函数即工具"——写个普通 Python 函数，框架用 `inspect.signature` 自动生成 JSON Schema，零胶水代码：

```python
# ADK-Python: src/google/adk/tools/function_tool.py（节选）
class FunctionTool(BaseTool):
def __init__(self, func: Callable[..., Any]):
self.func = func
self._declaration = build_function_declaration(func=func)  # 自动从签名生成
```

**Hermes** 更进一步，用 **AST 静态分析** 扫描 `tools/` 目录自动发现工具——新增工具 = 新增文件，因为 AST 解析不会触发 `import` 的副作用（如依赖 Docker/GPU）。

### 我们怎么实现

我们的 `tools/registry.py` 同样走"函数即工具 + 自动 Schema"路线，用**装饰器**注册：

```python
# agents/tools/registry.py
"""工具注册中心 —— 声明式 + 自动 Schema 生成"""
import inspect
import json
from typing import Any, Callable, Optional

_TOOL_REGISTRY: dict[str, Callable] = {}
_MANUAL_SCHEMAS: dict[str, dict] = {}  # 手动指定的复杂 schema

def tool(description: str = ""):
"""装饰器：注册一个函数为 Agent 工具。"""
def decorator(func: Callable) -> Callable:
func._tool_description = description or func.__doc__ or ""
_TOOL_REGISTRY[func.__name__] = func
return func
return decorator

def register_tool_manual(name: str, func: Callable, schema: dict):
"""手动注册一个工具，适用于复杂嵌套类型的 schema。"""
_TOOL_REGISTRY[name] = func
_MANUAL_SCHEMAS[name] = schema
```

**核心：从函数签名自动生成 OpenAI Function Calling Schema**——这与 ADK 的 `build_function_declaration` 异曲同工：

```python
# agents/tools/registry.py（续）
def get_tool_schemas() -> list[dict]:
"""自动从注册的函数生成 OpenAI Function Calling Schema。
对于手动注册的工具，优先使用指定的 schema。"""
schemas = []
for name, func in _TOOL_REGISTRY.items():
if name in _MANUAL_SCHEMAS:        # 复杂嵌套类型走手动 schema
schemas.append(_MANUAL_SCHEMAS[name])
continue

sig = inspect.signature(func)      # ← 关键：反射读取函数签名
properties = {}
required = []
for param_name, param in sig.parameters.items():
if param_name in ("self", "cls"):
continue
prop = {"type": _python_type_to_json_type(param.annotation)}
properties[param_name] = prop
if param.default is inspect.Parameter.empty:
required.append(param_name)  # 无默认值 → 必填
schemas.append({
"type": "function",
"function": {
"name": name,
"description": func._tool_description,
"parameters": {"type": "object",
"properties": properties, "required": required},
},
})
return schemas
```

工具的实际执行也封装在这里，并统一了成功/失败的返回结构：

```python
# agents/tools/registry.py（续）
def execute_tool(name: str, arguments: str) -> Any:
"""执行工具并返回结果。"""
func = _TOOL_REGISTRY.get(name)
if not func:
return {"error": f"未知工具: {name}"}
try:
args = json.loads(arguments) if isinstance(arguments, str) else arguments
result = func(**args)
return {"success": True, "result": result}
except Exception as e:
return {"success": False, "error": str(e)}

def _python_type_to_json_type(annotation) -> str:
type_map = {str: "string", int: "integer", float: "number",
bool: "boolean", list: "array"}
return type_map.get(annotation, "string")
```

### 内置工具：写个函数就能用

有了装饰器，新增工具真的只需要写个带 `@tool` 的函数：

```python
# agents/tools/builtin/__init__.py
"""示例工具集"""
import os
from tools.registry import tool

@tool("读取指定路径的文件内容")
def read_file(path: str) -> str:
with open(path, 'r', encoding='utf-8') as f:
return f.read()

@tool("将内容写入指定路径的文件")
def write_file(path: str, content: str) -> str:
with open(path, 'w', encoding='utf-8') as f:
f.write(content)
return f"文件已写入: {path}"

@tool("在目录中搜索包含关键词的文件")
def search_files(directory: str, keyword: str) -> list[str]:
results = []
for root, dirs, files in os.walk(directory):
for file in files:
filepath = os.path.join(root, file)
try:
with open(filepath, 'r', encoding='utf-8') as f:
if keyword in f.read():
results.append(filepath)
except (UnicodeDecodeError, PermissionError):
continue
return results
```

### 为什么需要"手动 Schema"这条旁路？

自动 Schema 只能推断到 `list → array`，无法描述**嵌套结构**（如 Planning 的 `steps: [{id, description, status}]`）。
所以我们保留了 `register_tool_manual` 旁路。这对应 ADK 用 `enum` 约束 `transfer_to_agent` 目标的思路——
**当自动推断不够精确时，手写 Schema 来约束模型，减少幻觉**。具体例子见下面 Planning 章节。

### 设计取舍

| 维度        | 业界                             | 本项目                        |
| ----------- | -------------------------------- | ----------------------------- |
| 注册方式    | ADK 传函数 / Hermes AST 发现     | `@tool` 装饰器              |
| Schema 生成 | `inspect.signature` 自动       | 自动 + 手动旁路               |
| 复杂类型    | ADK enum 约束                    | `register_tool_manual` 手写 |
| 并发调度    | Claude Code`isConcurrencySafe` | 串行执行（教学简化）          |

---

## 四、上下文管理：分级递进压缩

### 业界怎么做

这是各家差异最大的地方。**Claude Code** 的五级递进压缩堪称教科书：

| 级别 | 名称               | 触发条件  | 代价                                 |
| ---- | ------------------ | --------- | ------------------------------------ |
| L0   | Tool Output Budget | 每轮      | 零 LLM 开销（超限持久化到磁盘）      |
| L1   | Snip Compact       | 每轮      | 零开销（裁剪最早消息）               |
| L2   | MicroCompact       | 每轮      | 零开销（按时间衰减压缩 tool_result） |
| L3   | Context Collapse   | ~85% 窗口 | 一次 LLM 调用                        |
| L4   | AutoCompact        | ~90% 窗口 | 较重 LLM 调用                        |

**核心思想**：不是一上来就全量压缩，而是**从轻到重逐级尝试**——能用规则（零成本）解决就不调 LLM。

**Codex** 则用差分注入（增量上下文）+ "摘要 + 保留最近用户消息" 的策略。

### 我们怎么实现

我们的 `context/manager.py` 实现了**两级**压缩，正是 Claude Code 五级思想的精炼版：

```python
# agents/context/manager.py
"""上下文窗口管理 —— 分级压缩策略"""
import json
from dataclasses import dataclass
from typing import Optional
from openai import OpenAI

@dataclass
class ContextConfig:
max_tokens: int = 128000
compress_threshold: float = 0.7      # 超过 70% → Level 1 规则压缩
summary_threshold: float = 0.9       # 超过 90% → Level 2 摘要压缩
max_tool_result_tokens: int = 8000   # 单个工具结果上限

class ContextManager:
"""分级上下文管理器。"""

def manage(self, messages: list[dict]) -> list[dict]:
"""根据当前 token 使用率执行分级压缩。"""
usage = self._estimate_usage(messages)
if usage < self.config.compress_threshold:
return messages                       # 没超阈值 → 不动
if usage < self.config.summary_threshold:
return self._rule_compress(messages)  # Level 1
return self._summary_compress(messages)   # Level 2
```

**Level 1 = 规则压缩（对应 Claude Code 的 L0~L2，零 LLM 开销）：** 截断大工具结果 + 丢弃旧消息：

```python
# agents/context/manager.py（续）
def _rule_compress(self, messages: list[dict]) -> list[dict]:
"""Level 1: 规则压缩 —— 截断大型工具结果、移除旧消息。"""
compressed = []
for msg in messages:
if msg["role"] == "system":
compressed.append(msg)
continue
if msg["role"] == "tool":
content = msg.get("content", "")
if len(content) > self.config.max_tool_result_tokens * 4:
msg = {**msg,
"content": content[:self.config.max_tool_result_tokens * 4]
+ "\n\n[... 结果已截断，如需完整内容请重新调用工具 ...]"}
compressed.append(msg)

# 截断后仍超阈值 → 进一步丢弃最早的消息（保留最近 60%）
if self._estimate_usage(compressed) > self.config.compress_threshold:
system_msgs = [m for m in compressed if m["role"] == "system"]
other_msgs = [m for m in compressed if m["role"] != "system"]
keep_count = max(4, int(len(other_msgs) * 0.6))
start_idx = len(other_msgs) - keep_count
# ← 关键修复：向前扩展，确保不拆散 assistant(tool_calls) + tool 配对
while start_idx > 0 and start_idx < len(other_msgs):
if other_msgs[start_idx]["role"] == "tool":
start_idx -= 1
else:
break
compressed = system_msgs + other_msgs[start_idx:]
return compressed
```

> **踩坑警示**：上面 `while` 循环的"配对保护"是血泪教训。OpenAI API 要求
> `role=tool` 消息必须紧跟其对应的 `assistant(tool_calls)` 消息。如果裁剪边界恰好落在
> `tool` 消息上，就会拆散这组配对，直接报错：
> `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。
> 这对应 Claude Code 选择 `direction: 'from'` 压缩方向时同样要保护消息完整性。

**Level 2 = 摘要压缩（对应 Claude Code 的 L3/L4，一次 LLM 调用）：** 用 LLM 把旧历史熔炼成摘要，这正是 Codex "摘要 + 保留最近用户消息" 策略的体现：

```python
# agents/context/manager.py（续）
def _summary_compress(self, messages: list[dict]) -> list[dict]:
"""Level 2: 摘要压缩 —— 用 LLM 生成对话摘要。"""
system_msgs = [m for m in messages if m["role"] == "system"]
other_msgs = [m for m in messages if m["role"] != "system"]

split_idx = len(other_msgs) - 4           # 保留最近 4 条
while split_idx > 0 and split_idx < len(other_msgs):
if other_msgs[split_idx]["role"] == "tool":
split_idx -= 1                     # 同样的配对保护
else:
break

old_msgs = other_msgs[:split_idx]
recent_msgs = other_msgs[split_idx:]
if not old_msgs:
return messages

summary_response = self.client.chat.completions.create(
model="deepseek-v4-flash",
messages=[
{"role": "system", "content": (
"请将以下对话历史压缩为结构化摘要。"
"保留关键信息：用户意图、已完成操作、重要结果、待处理事项。")},
{"role": "user", "content": json.dumps(old_msgs, ensure_ascii=False)},
],
)
summary = summary_response.choices[0].message.content
summary_msg = {"role": "user", "content": f"[历史摘要]\n{summary}\n[/历史摘要]"}
return system_msgs + [summary_msg] + self._truncate_tool_results(recent_msgs)

def _estimate_usage(self, messages: list[dict]) -> float:
"""估算 token 使用率（粗略：4 字符 ≈ 1 token）。"""
total_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
return (total_chars / 4) / self.config.max_tokens
```

注意 Level 2 摘要 prompt 让模型保留"用户意图、已完成操作、重要结果、待处理事项"——
这与 Codex 压缩 prompt 要求的"当前进度、关键决策、剩余工作、关键数据"完全一致，都是
**结构化 handoff 摘要**，比简单截断头部丢失的信息少得多。

### 设计取舍

| 维度         | Claude Code      | Codex                 | 本项目             |
| ------------ | ---------------- | --------------------- | ------------------ |
| 级数         | 5 级（L0~L4）    | 三阶段触发            | 2 级               |
| 零成本压缩   | L0~L2            | 工具输出截断          | Level 1 规则压缩   |
| LLM 摘要     | L3/L4            | Local/Remote 三路分发 | Level 2            |
| 消息配对保护 | direction:'from' | normalize 三重保障    | `while` 向前回退 |

---

## 五、流式输出：边想边说的体验

### 业界怎么做

Claude Code 用 `AsyncGenerator` 逐条 `yield` 消息，调用方边收边渲染。
SSE（Server-Sent Events）是前端常见的流式协议。

### 我们怎么实现

`agent_loop_streaming` 逐 chunk 解析 LLM 的流式响应。**难点在于工具调用的组装**——
DeepSeek/OpenAI 会把同一个 `tool_call` 的 `name` 和 `arguments` 拆成多个 chunk 分批发送，
需要用 `tool_calls_buffer` 按 `index` 累积拼接：

```python
# agents/agent/core.py（逐 chunk 处理部分）
full_content = ""
tool_calls_buffer: dict[int, dict] = {}

for chunk in stream:
delta = chunk.choices[0].delta

# 文本增量 → 实时 yield 给前端
if delta.content:
full_content += delta.content
yield {"type": "text_delta", "content": delta.content}

# 工具调用增量 → 按 index 累积组装
if delta.tool_calls:
for tc in delta.tool_calls:
idx = tc.index
if idx not in tool_calls_buffer:
tool_calls_buffer[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
if tc.id:
tool_calls_buffer[idx]["id"] = tc.id
if tc.function and tc.function.name:
tool_calls_buffer[idx]["name"] = tc.function.name
if tc.function and tc.function.arguments:
tool_calls_buffer[idx]["arguments"] += tc.function.arguments
# 无工具调用 → 这是最终回答
if not tool_calls_buffer:
messages.append({"role": "assistant", "content": full_content})
yield {"type": "done", "content": full_content}
return
```

工具执行阶段，每个工具调用都 `yield` 出 `tool_start` / `tool_result` 两个事件，前端就能实时显示"正在调用 xxx 工具"：

```python
# agents/agent/core.py（工具执行部分）
# 先把 assistant(tool_calls) 消息加入历史
messages.append({
"role": "assistant",
"content": full_content,
"tool_calls": [
{"id": info["id"], "type": "function",
"function": {"name": info["name"], "arguments": info["arguments"]}}
for info in tool_calls_buffer.values()
],
})

plan_updated = False
for _idx, tc_info in tool_calls_buffer.items():
tool_name = tc_info["name"]
yield {"type": "tool_start", "tool": tool_name, "args": tc_info["arguments"]}

result = execute_tool(tool_name, tc_info["arguments"])
result_str = json.dumps(result, ensure_ascii=False)

yield {"type": "tool_result", "tool": tool_name,
"result": str(result)[:200] + ("..." if len(str(result)) > 200 else "")}

# role=tool 消息必须紧跟在 assistant(tool_calls) 后面
messages.append({"role": "tool", "tool_call_id": tc_info["id"], "content": result_str})

if tool_name in ("update_plan", "mark_step"):
plan_updated = True
```

消费端（`main.py`）只需按事件类型分发渲染：

```python
# agents/main.py（事件渲染部分）
for event in agent_loop_streaming(messages=messages, client=client,
ctx_mgr=ctx_mgr, plan_mgr=plan_mgr, model=MODEL):
etype = event["type"]
if etype == "text_delta":
print(event["content"], end="", flush=True)   # 逐字打印
elif etype == "thinking":
print(f"\n  💭 {event['content']}")
elif etype == "tool_start":
print(f"\n  🔧 调用工具: {event['tool']}({event['args'][:60]})")
elif etype == "tool_result":
print(f"  📋 结果: {event['result']}")
elif etype == "done":
print()
```

> **核心收获**：流式的本质是「事件协议」。我们定义了 `thinking / text_delta / tool_start / tool_result / plan / done / max_steps` 七种事件类型，
> 生成器产生事件、消费者渲染事件，二者彻底解耦——换成 Web 前端只需把 `print`
> 改成 SSE 推送即可。

---

## 六、Planning：todo.md 任务计划

### 业界怎么做

**Manus** 和 **Codex** 都用一个简单的 Markdown 文件作为"任务计划"，
让 Agent 先规划再执行。复杂任务先 `update_plan` 列出步骤，执行中用 `mark_step` 更新状态。

### 我们怎么实现

`agent/planner.py` 的 `PlanManager` 维护一个有状态的步骤列表，并渲染为 Markdown：

```python
# agents/agent/planner.py
class PlanManager:
"""管理 Agent 的执行计划 —— 有状态的单例。"""

def __init__(self):
self.steps: list[dict] = []

def update(self, steps: list[dict]) -> str:
self.steps = steps
return self._render()

def mark_step(self, step_id: int, status: str) -> str:
for step in self.steps:
if step["id"] == step_id:
step["status"] = status
break
return self._render()

def _render(self) -> str:
"""渲染为 Markdown 格式，注入到上下文中。"""
if not self.steps:
return "📋 当前无执行计划。"
status_icons = {"pending": "⬜", "in_progress": "🔄", "done": "✅", "failed": "❌"}
lines = ["## 📋 执行计划\n"]
for step in self.steps:
icon = status_icons.get(step["status"], "⬜")
desc = step.get("description", f"步骤 {step['id']}")
lines.append(f"{icon} {step['id']}. {desc}")
return "\n".join(lines)
```

**这里就用上了第三章提到的"手动 Schema"旁路**——因为 `steps` 是嵌套数组，自动推断不出内部结构：

```python
# agents/agent/planner.py（手动 Schema）
UPDATE_PLAN_SCHEMA = {
"type": "function",
"function": {
"name": "update_plan",
"description": "创建或更新任务执行计划。计划应包含多个步骤，不允许只有一步。",
"parameters": {
"type": "object",
"properties": {
"steps": {
"type": "array",
"items": {
"type": "object",
"properties": {
"id": {"type": "integer"},
"description": {"type": "string"},
"status": {"type": "string",
"enum": ["pending", "in_progress", "done", "failed"]},
},
"required": ["id", "description", "status"],
},
},
},
"required": ["steps"],
},
},
}
```

**用闭包把工具函数绑定到有状态的 `PlanManager` 实例上**——这是连接"无状态工具注册表"与"有状态管理器"的关键技巧：

```python
# agents/agent/planner.py（闭包注册）
def register_planning_tools(plan_mgr: PlanManager):
"""通过闭包捕获 plan_mgr，使得工具函数能读写计划状态。"""
def _update_plan(steps: list) -> str:
return plan_mgr.update(steps)
def _mark_step(step_id: int, status: str) -> str:
return plan_mgr.mark_step(step_id, status)

register_tool_manual("update_plan", _update_plan, UPDATE_PLAN_SCHEMA)
register_tool_manual("mark_step", _mark_step, MARK_STEP_SCHEMA)
return plan_mgr
```

计划变更后，主循环会把最新计划**注入回上下文**，让模型始终"看得见"自己的进度（见 `core.py`）：

```python
# agents/agent/core.py（计划注入）
if plan_updated and not plan_mgr.is_empty():
plan_summary = plan_mgr.get_progress_summary()
yield {"type": "plan", "content": plan_summary}
messages.append({
"role": "user",
"content": f"[当前计划]\n{plan_mgr._render()}\n[/当前计划]\n{plan_summary}",
})
```
