# MiniCode M3 架构

MiniCode 0.4.0 是一个本地 CLI Coding Agent。模型负责决策，工具层负责执行，策略层限制能力，事件和 checkpoint 让运行可检查、可恢复。

```text
用户任务
  -> CLI / M4 Web REST+SSE / 启动脚本
  -> CodingAgent 循环
       -> DeepSeek 流式模型
       -> 工具注册表与策略校验
       -> 仓库读取 / Patch / 测试 / Diff
       -> EventSink -> events.jsonl -> Replay / 指标
       -> RunStore  -> SQLite + JSON checkpoint -> resume
```

关键边界：

- `provider.py` 只处理模型协议、流式 chunk、tool call 和 usage；
- `agent.py` 编排模型、工具、完成协议和事件，不直接执行 Shell；
- `tools/` 校验路径、命令和 Patch，再调用工作区能力；
- `context.py` 将大结果转为 artifact，并压缩较旧上下文；
- `persistence.py` 保存可恢复状态，并在恢复前核对工作区指纹；
- `replay.py` 只读取 Trace，重建时间线与确定性指标；
- `security.py` 对当前策略边界运行版本化离线探针；
- `eval_m3.py` 物化 20 个任务，以 Agent 不可见的隐藏测试独立评分。
- `web.py` 将现有 RunStore 和事件协议暴露为仅限本机的 REST/SSE 控制台；
- `subagents.py` 定义 M5 Explore child Agent；它拥有隔离消息和只读工具，将大量探索上下文压缩为经过路径/行号校验的报告。
- `ExploreDelegationConfig` 将 child Provider 和预算注入 Coding 工具层；父 Agent 只收到报告，并由工具状态强制执行“复核证据后才能 Patch”。

数据默认写在目标工作区的 `.mini-code/`。源码、工具返回和 Patch 可能进入本地 Trace、checkpoint 或 artifact，因此这些运行产物不应直接公开。
