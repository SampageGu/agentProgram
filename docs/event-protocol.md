# M3 事件协议

每行 Trace 是一个 UTF-8 JSON 对象，位于 `.mini-code/runs/<run_id>/events.jsonl`：

```json
{"type":"model_completed","run_id":"...","data":{"message":"...","duration_ms":321,"usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}},"timestamp":"2026-08-31T12:00:00.000+00:00"}
```

公共字段为 `type`、`run_id`、`data`、`timestamp`。M3 仍允许向 `data` 增加字段；消费者必须忽略未知字段。

主要事件：

| 类别 | 事件 |
| --- | --- |
| 生命周期 | `run_started`、`run_resumed`、`run_completed`、`run_failed`、`run_interrupted`、`budget_exhausted` |
| 模型 | `model_started`、`text_delta`、`model_completed` |
| 工具 | `tool_started`、`tool_completed` |
| Coding | `plan_updated`、`patch_applied`、`verification_passed`、`verification_failed`、`completion_verified` |
| M2 状态 | `context_compacted`、`checkpoint_saved` |
| M5 child Trace | `subagent_started`、`subagent_completed`、`subagent_failed` |
| M5 parent Trace | `subagent_delegated`、`subagent_result`、`subagent_failed` |

`replay <run_id>` 按文件顺序重放事件，并从这些事实计算耗时、步骤、工具失败、Patch、测试、压缩、恢复、改动文件和 Token。它不会重新调用模型或重新执行工具，因此是审计回放，不是行为复现。M4 Web 按 JSONL 行号为事件附加从 1 开始的 `sequence`，SSE 使用相同值作为 `id`；客户端用 `after` 或 `Last-Event-ID` 续传，持久化事件本身保持兼容。
