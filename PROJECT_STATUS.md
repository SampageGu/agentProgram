# MiniCode Agent 项目状态

> 最后更新：2026-09-01（Asia/Shanghai）  
> 当前阶段：**M4.1 对话式 Web 等待人工验收**
> 下一阶段：**M5.3 父子 Replay 与聚合指标**

本文是公开仓库的项目进度单一事实来源，记录当前实际做到哪里、哪些验收已经通过以及下一步做什么。个人秋招分析与内部计划保存在本地，不进入公开仓库。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| 未开始 | 尚无实现 |
| 开发中 | 已开始编码，但未满足自动验收 |
| 待人工验收 | 实现和离线测试通过，等待用户亲自运行真实场景 |
| 已通过 | 自动验收和规定的人工验收都通过 |
| 阻塞 | 存在明确阻塞条件，当前无法继续 |

## 里程碑总览

| 阶段 | 目标 | 实现状态 | 自动验收 | 人工验收 | 总状态 |
| --- | --- | --- | --- | --- | --- |
| M0 | 只读仓库分析 Agent | 已完成 | 10/10 通过 | 待执行 | **待人工验收** |
| M0.1 | 终端连续对话，进程内保留上下文 | 已完成 | 12/12 通过 | 待执行 | **待人工验收** |
| M1 | Patch、命令、测试与失败修复 | 已完成 | 19/19 通过 | 待执行 | **待人工验收** |
| M2 | 上下文压缩、checkpoint、resume | 已完成 | 22/22 通过 | 待执行 | **待人工验收** |
| M3 | 安全对抗、Replay、完整评测 | 已完成 | 27/27 + 15/15 通过 | 待执行 | **待人工验收** |
| M4 | REST/SSE 本地可视化控制台 | 已完成 | 29/29 + HTTP smoke 通过 | 待执行 | **待人工验收** |
| M5.1 | 独立只读 Explore Subagent | 已完成 | 34/34 + 真实 smoke 通过 | 待用户复跑 | **待人工验收** |
| M5.2 | 主 Agent 委派与父子 Run | 已完成 | 专项通过；当前全量 39/39 | 待执行 | **待人工验收** |
| M4.1 | ChatGPT 式多轮 Web 与 Subagent 卡片 | 已完成 | 39/39 + HTTP/SSE 通过 | 待执行 | **待人工验收** |

## M0 当前结果

### 已完成

- [x] 零依赖启动器 `run_mini_code.py`；
- [x] Windows 简易启动入口 `start.cmd`，支持 chat/ask/test/eval/trace/help，兼容受限 PowerShell 执行策略；
- [x] DeepSeek OpenAI-compatible 流式客户端；
- [x] 默认模型 `deepseek-v4-flash`；
- [x] `list_files` 目录浏览工具；
- [x] `search_code` 代码搜索工具；
- [x] `read_file` 按行读取工具；
- [x] 流式文本和结构化 Agent 事件；
- [x] 流式 tool call chunk 拼接；
- [x] 最大 Agent step 预算；
- [x] 工作区路径边界；
- [x] `.env`、`.git`、`.mini-code` 工具访问保护；
- [x] JSONL Trace 持久化；
- [x] 5 题真实模型评测集和自动评分器；
- [x] M0–M5.2 中文统一验收指南。

### 自动验收记录

2026-08-31 执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：

```text
Ran 10 tests in 0.345s
OK
```

结论：**M0 离线自动验收通过。**

### 尚待用户手动验收

- [ ] 使用真实 DeepSeek Key 完成一次 `ask`；
- [ ] 确认文本为流式输出；
- [ ] 确认至少发生一次工具调用；
- [ ] 确认最终回答包含真实 `path:line` 引用；
- [ ] 使用 `trace <run_id>` 查看完整事件；
- [ ] 确认 Trace 不包含 API Key；
- [ ] 运行 `eval-m0`；
- [ ] 真实模型评测至少 4/5 PASS，且无 Provider/工具运行时错误；
- [ ] 确认 Agent 未修改被分析仓库。

由于当前未发现 `.mini-code/runs` 或 `.mini-code/evals` 产物，上述真实模型验收不能提前标记为通过。

### M0 人工验收命令

```powershell
.\.venv\Scripts\python.exe run_mini_code.py ask `
  "M0 向模型提供了哪些只读工具？分别有什么作用？请引用代码行" `
  --workspace .
```

成功后记录输出的 Run ID，再执行：

```powershell
.\.venv\Scripts\python.exe run_mini_code.py trace <run_id> --workspace .
.\.venv\Scripts\python.exe run_mini_code.py eval-m0 --workspace .
```

详细判定规则见 [docs/验收指南.md](docs/验收指南.md)。

## M0.1 当前结果

目标命令：

```powershell
.\.venv\Scripts\python.exe run_mini_code.py chat --workspace .
```

已完成功能：

- [x] REPL 连续输入；
- [x] 同一进程内保留用户、助手和工具消息；
- [x] 后续问题可以引用“刚才的文件”“上一个结果”；
- [x] `/help`；
- [x] `/history`；
- [x] `/clear`；
- [x] `/exit`；
- [x] CLI 流式渲染层规范化 Markdown 转义和加粗星号；
- [x] 每轮生成独立 Run ID 并保存结构化事件；
- [x] 增加多轮上下文和跨 chunk 渲染自动测试；
- [x] 增加 M0.1 手动验收用例。

自动验收：2026-08-31 执行全量测试，结果 `Ran 12 tests`、`OK`。

人工验收：待用户使用真实 DeepSeek Key 执行，步骤见 [docs/验收指南.md](docs/验收指南.md)。

M0.1 只做进程内连续对话。退出后恢复、长期会话持久化和上下文压缩属于 M2。

## M1 当前结果

目标命令：

```powershell
.\start.cmd run "修复一个 Bug 并运行测试"
```

已完成：

- [x] `update_plan`，同时最多一个 `in_progress`；
- [x] `apply_patch`，只接受 unified diff，修改前校验全部 hunk；
- [x] 禁止 Patch 越界、访问 `.env`、删除、重命名和二进制文件；
- [x] `run_command` 参数数组执行，无 Shell，限制 Python 模块和 Git 子命令；
- [x] `run_tests` 自动选择 unittest/pytest，限制超时和输出；
- [x] 子进程环境过滤 Key、Token、Secret、Password 等变量；
- [x] `git_diff` 输出仓库状态与本轮 session diff；
- [x] `finish_task` 要求计划完成、确有修改、最新修改后测试通过、最新 Diff 已查看；
- [x] 测试失败后继续 Patch 的端到端 Agent Loop；
- [x] plan、patch、verification、completion 结构化事件；
- [x] 一次性 `demo-m1`；
- [x] 三个版本化单文件 Bug 评测与独立 grader 测试；
- [x] M1 自动测试与人工验收说明。

自动验收：2026-08-31 执行全量测试，结果 `Ran 19 tests`、`OK`。其中端到端用例真实执行“错误 Patch → 测试失败 → 再次 Patch → 测试通过 → Diff → finish_task”。

人工验收：待用户运行 `.\start.cmd demo` 和 `.\start.cmd eval1`。正式通过标准见 [docs/验收指南.md](docs/验收指南.md)。

## M2 当前结果

已完成：

- [x] SQLite `state.db` 保存每个 run 的最新可恢复状态；
- [x] 每个关键步骤另存版本化 JSON checkpoint；
- [x] checkpoint 包含消息、计划、修改文件、Patch 基线、测试、Diff 与步骤预算；
- [x] 大工具输出写入 `artifacts/*.txt`，上下文保存摘要和引用；
- [x] 旧工具结果、旧助手文本和大型 Patch 参数渐进式压缩；
- [x] 系统上下文持续保留任务状态、当前计划、修改文件、最新失败与 Diff 摘要；
- [x] `resume <run_id>` 恢复完整工具状态和消息，从下一步继续；
- [x] Provider 失败、用户中断和步骤预算耗尽均保留最近 checkpoint；
- [x] 恢复前工作区 SHA-256 指纹校验；
- [x] 外部修改冲突时退出码 3，拒绝继续修改；
- [x] `status <run_id>` 查看持久化状态；
- [x] `demo2` 可重复演示预算暂停、状态查询和恢复；
- [x] M2 持久化、压缩、artifact、冲突和恢复端到端测试。

自动验收：2026-08-31 执行全量测试，结果 `Ran 22 tests`、`OK`。恢复用例在 Patch 后模拟 Provider 中断，随后从 checkpoint 继续测试、Diff 和完成协议，且没有重复 Patch。

人工验收：待用户使用真实 DeepSeek 执行 `.\start.cmd demo2`，再用输出的 Run ID 和 Demo workspace 执行 `status`、`resume` 与 `trace`。

## M3 当前结果

已完成：

- [x] `replay <run_id>` 时间线与确定性运行指标；
- [x] DeepSeek 流式 usage 采集，报告 Token；
- [x] 可选输入/输出单价快照与成本估算；
- [x] 15 个路径、秘密文件、Shell、网络、Git 写入和未知工具安全探针；
- [x] 一次性模型故障注入；
- [x] Provider 中断后的 checkpoint/resume 恢复测试；
- [x] 20 个任务、8 类问题、独立隐藏 grader 的版本化评测；
- [x] 成功率、回归率、无关修改率、步骤、P50/P95、Token 和成本报告；
- [x] 架构、事件协议、威胁模型和评测说明。

自动验收：2026-08-31 执行全量测试，结果 `Ran 27 tests`、`OK`；执行安全审计，15/15 版本化探针被阻断；并用已有 Run 验证 Replay，可将数百个流式 chunk 合并为可读摘要。自动验收不调用模型。

人工验收：待用户依次运行 `security`、一次真实 Coding 任务、`replay`、故障注入与 `resume`，最后先执行 1 题、再决定是否执行完整 20 题真实评测。完整评测会消耗 API Token。

## M4 当前结果

框架定义：本地单用户、单固定 workspace 的 Web 控制台，复用现有 CodingAgent、事件协议和 RunStore，不在 Web 层复制 Agent Loop。

已完成：

- [x] `web` / `webdemo` 一键启动；
- [x] REST 健康检查、Run 列表、详情、创建、事件与取消接口；
- [x] SSE 以稳定 sequence/Last-Event-ID 语义续传事件；
- [x] 计划、改动文件、测试、完成协议、步骤、工具、耗时与 Token 页面；
- [x] 页面刷新后从持久化 Trace 恢复；
- [x] 协作式取消并保存可 resume 的 cancelled checkpoint；
- [x] 只允许 localhost、固定 workspace、CSP 等安全响应头；
- [x] 响应式、零前端依赖页面；
- [x] M0–M5.2 统一人工验收指南。

自动验收：2026-08-31 全量 `29/29` 通过；真实启动 `http://127.0.0.1:8765` 后，health、首页、CSP 和 Run 列表 API 均通过；测试服务器验证 POST 创建和 SSE 格式。未在 MiniCode 根目录提交真实修改任务，以免验收污染项目源码。

人工验收：待用户运行 `.\start.cmd webdemo`，在一次性 workspace 中完成正常 Run、刷新恢复和取消/恢复。

## M4.1 当前结果

已完成：

- [x] SQLite 持久化 `Conversation → 多个 Run` 关系，刷新和服务重启后仍可恢复；
- [x] 左侧会话历史、中央多轮消息、底部固定输入框和右侧运行详情抽屉；
- [x] 首轮创建会话，后续消息复用同一 workspace，并把最近任务摘要交给新 Run；
- [x] SSE 流式回答，工具调用折叠卡片，Explore Subagent 独立卡片；
- [x] 计划、测试、Diff、Token、checkpoint 和事件仍可实时查看；
- [x] 停止按钮、移动端侧栏和刷新续传；
- [x] 新增 Conversation 持久化和 REST 接口自动测试。

自动验收：2026-09-01 全量 `39/39` 通过，HTTP 测试覆盖会话创建、继续对话、静态页面和 SSE。当前环境未安装 Node.js，因此未额外运行 `node --check`；前端由浏览器人工验收覆盖。

人工验收：待用户运行 `.\start.cmd webdemo`，完成“首轮修复 → 同一会话追问补测试 → 刷新恢复 → 展开 Subagent 卡片”。

## M5.1 当前结果

已完成：

- [x] 独立 `EXPLORE_SYSTEM_PROMPT` 与 `explore-v2` Prompt 版本；
- [x] 独立 child Run ID、消息上下文、最多 1—8 步预算和 JSONL Trace；
- [x] 仅暴露 `list_files`、`search_code`、`read_file`，没有写入、命令或递归委派能力；
- [x] summary、evidence、root cause、next steps、risks 结构化报告；
- [x] evidence 相对路径、保护边界、文件存在和真实行号校验；
- [x] `exploration-report.json` 保存 parent/child ID 与委派任务；
- [x] `subagent_started/completed/failed` 事件；
- [x] child 24K 字符上下文压缩、4K artifact 阈值与 64K 硬 Token 预算；
- [x] `explore` CLI 与简易启动命令；
- [x] 正常报告、非法报告、秘密路径、独立预算四类测试。

自动验收：2026-09-01 全量 `34/34` 通过。第一次真实 Run `explore-67d9da8d181b` 因 6 步预算耗尽而失败；调整为 `explore-v2` 和 8 步后，真实 Run `explore-ddaa18259f49` 成功生成含 14 条证据的报告。该成功基线用了 8 步、16 次工具调用和 72,663 Token，因此随后加入 24K 上下文压缩与 64K 硬 Token 预算；预算控制由离线测试验证，未为此继续消耗第三次真实调用。M5.1 当时仅提供独立 child；主 Agent 委派现已在 M5.2 补齐。

人工验收：待用户执行 `.\start.cmd explore "定位 M4 Web 服务入口和安全边界"`，核对真实 DeepSeek child Trace 和报告。

## M5.2 当前结果

已完成：

- [x] CodingAgent 在 CLI、resume 和 M4 Web Run 中获得 `delegate_explore`；
- [x] 父 Agent 使用独立 Provider 创建 child，不共享消息上下文；
- [x] 父 Trace 记录 `subagent_delegated`、`subagent_result/failed`；
- [x] child Trace 与报告保存 `parent_run_id`；
- [x] child 只把结构化报告返回父上下文，不返回内部完整消息；
- [x] 每个父 Run 最多两次委派，默认 child 为 6 步/32K Token；
- [x] child 失败后返回明确错误，父 Agent 可直接 search/read 降级；
- [x] 有成功 child 报告时，父 Agent 必须重新读取至少一个证据文件，才能 Patch；
- [x] 委派次数、报告、证据与复核状态进入 checkpoint，resume 后保持；
- [x] `--no-subagents` 支持单 Agent 对照运行；
- [x] 完整 Fake Parent → Child → 父复核 → Patch → 测试 → Diff → finish 端到端测试。

自动验收：M5.2 完成时全量 `37/37` 通过；加入 M4.1 会话测试后，当前项目基线为 `39/39`。尚未执行真实 DeepSeek 父子 Coding Demo，避免在未告知用户的情况下继续产生两层模型调用成本。

人工验收：待用户运行 `.\start.cmd demo`，确认父 Trace 和 child Trace 均存在、父端复核发生在 Patch 之前，并与 `--no-subagents` 运行对照。

## 当前已知限制

- `ask` 仍是单次任务；连续追问需使用 `chat` 或直接运行 `.\start.cmd`；
- `chat` 的会话历史仍只在进程内；M2 checkpoint/压缩当前用于 Coding `run`，尚未接入只读聊天；
- M1 可修改文本文件，但不支持删除、重命名、二进制 Patch 或 Git commit/push；
- 命令通过白名单且不经过 Shell，但尚无容器/虚拟机级沙箱；被运行的仓库测试代码仍拥有当前用户的文件和网络权限；
- 当前 Python 验证适配最完整，其他语言的测试适配器尚未实现；
- 上下文预算当前按序列化字符数估算，不是模型 tokenizer 的精确 Token 数；
- checkpoint 保存源码基线和工具观察，可能包含私有代码，只应保存在本机；
- `resume` 使用严格工作区指纹；外部修改后不会自动三方合并；
- 尚无按墙钟时间或 API Token 用量限制整次 run 的预算；M3 仅统计 API 返回的 usage；
- 成本估算依赖用户填写的单价快照，未配置或供应商未返回 usage 时为 `null`；
- 安全审计 15/15 只覆盖当前探针，不代表操作系统级隔离；
- M3 评测均为小型 Python 函数，不能代表大型、多语言仓库表现；
- M4 是本地单用户控制台，没有登录、多租户、远程部署或人工审批队列；
- M4 取消为协作式取消，不能中途强杀正在进行的 HTTP 模型请求或测试子进程；
- M5.2 父子委派已接入 CLI 与 Web 后端，但真实模型的成本和成功率尚未形成对照报告；
- M5.2 已能真正委派，M4.1 页面可以展示 Subagent 卡片；但 Replay 仍需分别传入父/child Run ID，父子 Token/耗时尚未聚合，这部分属于 M5.3；
- Trace 会记录工具返回的代码内容，不能用于公开包含私有代码的运行记录；
- M0 真实模型评测尚未由用户执行；
- `pyproject.toml` 的可编辑安装依赖打包后端，当前推荐直接使用零依赖启动器。

## 更新规则

每次开发完成后必须同步更新本文件：

1. 修改“最后更新”和“当前阶段”；
2. 更新里程碑总览；
3. 只在测试真实运行后填写自动验收结果；
4. 只在用户反馈手动运行成功后勾选人工验收；
5. 记录真实失败和已知限制，不提前宣称完成；
6. 下一阶段开始时写明目标命令和验收标准。

## 变更记录

| 日期 | 变更 | 验收结果 |
| --- | --- | --- |
| 2026-08-29 | 完成 M0 只读 Agent、工具、Trace、评测与验收文档 | 离线测试 10/10；真实模型待验收 |
| 2026-08-31 | 新增项目状态单一事实来源，明确 M0.1 连续对话为下一阶段 | 文档状态已与代码和运行产物核对 |
| 2026-08-31 | 新增 `start.ps1` 简易入口，隐藏虚拟环境、工作区和 `.env` 参数 | PowerShell 5.1 下 help/test 通过，离线测试 10/10 |
| 2026-08-31 | 记录 CLI Markdown 转义显示问题，并要求 M0 模型输出终端纯文本 | 提示层已修改；渲染层列入 M0.1 |
| 2026-08-31 | 完成 M0.1 多轮 REPL、进程内上下文、会话命令和流式输出规范化 | 离线测试 12/12；真实模型待人工验收 |
| 2026-08-31 | 新增 `start.cmd`，兼容禁止直接执行 `.ps1` 的 Windows 环境 | help 与无网络 REPL 烟雾测试通过 |
| 2026-08-31 | 完成 M1 Patch、受限进程、测试、Diff、Planning、完成协议和三题评测 | 离线测试 19/19；真实模型待人工验收 |
| 2026-08-31 | 完成 M2 SQLite/JSON checkpoint、artifact、上下文压缩、冲突检测和 resume | 离线测试 22/22；真实模型待人工验收 |
| 2026-08-31 | 完成 M3 Replay、usage/成本指标、安全探针、故障注入和 20 题隐藏测试评测 | 离线测试 27/27，安全探针 15/15；真实模型待人工验收 |
| 2026-08-31 | 定义并完成 M4 本地 REST/SSE 控制台、刷新恢复、协作式取消和 Web Demo | 离线测试 29/29，真实 HTTP smoke 通过；真实模型待人工验收 |
| 2026-09-01 | 完成 M5.1 独立只读 Explore Subagent、结构化证据协议、child Trace、上下文/Token 预算与 CLI | 离线测试 34/34；真实 smoke 先失败后通过，待用户复跑 |
| 2026-09-01 | 完成 M5.2 `delegate_explore`、父子事件、复核门禁、失败降级、resume 状态与单 Agent 开关 | 离线测试 37/37；真实父子 Demo 待人工验收 |
| 2026-09-01 | 完成 M4.1 持久化多轮会话、ChatGPT 式页面、工具/Subagent 卡片和详情抽屉 | 离线测试 39/39；浏览器人工验收待执行 |
