# MiniCode Agent

> 当前进度与下一步：[PROJECT_STATUS.md](PROJECT_STATUS.md)

MiniCode 是一个从零实现的 Coding Agent。当前 **M5.2 主 Agent → Explore Subagent 委派闭环已完成离线实现，等待真实 DeepSeek 人工验收**；它能够探索仓库、委派只读 child、复核证据、应用 Patch、运行测试，通过 checkpoint 恢复，并用 CLI 或 Web 查看实时运行。

```text
编程任务 → 仓库探索 → 计划 → Patch → 测试 → 失败修复 → Diff → 完成协议
```

## 当前已实现

- DeepSeek `deepseek-v4-flash` OpenAI-compatible 流式接口；
- `list_files`、`search_code`、`read_file` 三个只读工具；
- 流式文本、工具开始/结束、运行状态等结构化事件；
- 工具调用 chunk 的增量组装；
- 工作区路径边界，禁止访问工作区外、`.env`、`.git`、`.mini-code`；
- 最大 Agent step 预算；
- JSONL 完整 Trace；
- 进程内多轮上下文，可追问“刚才的文件”和“上一个结果”；
- `/help`、`/history`、`/clear`、`/exit` 会话命令；
- 终端流式输出规范化，不再直接显示 `\*\*` 一类转义标记；
- `update_plan` 多步骤计划；
- `apply_patch` unified diff 修改，拒绝越界、密钥文件、删除和重命名；
- `run_command` 无 Shell 的命令白名单；
- `run_tests` 支持 unittest/pytest，保存退出码和输出；
- `git_diff` 同时返回 Git 状态和本次 Agent 修改的 session diff；
- `finish_task` 验证优先完成协议：最新 Patch 后测试未通过或未查看 Diff时拒绝完成；
- SQLite 最新运行状态与逐步 JSON checkpoint；
- 大工具输出保存为 artifact，模型只接收包含引用的摘要；
- 超过上下文阈值后压缩旧工具结果和 Patch 参数，同时保留任务、计划、错误和 Diff 摘要；
- `resume` 从最近有效步骤继续，恢复计划、消息、修改基线和验证状态；
- 恢复前校验工作区指纹，外部修改时拒绝继续；
- `status` 查看持久化步骤、计划和修改文件；
- Trace Replay 与运行耗时、步骤、工具、测试、改动文件和 Token 指标；
- 15 个版本化安全策略探针与 JSON 审计报告；
- 单次模型故障注入，以及从 checkpoint 恢复的验证；
- 20 个、8 类 M3 Coding 任务，使用 Agent 不可见的隐藏测试评分；
- M4 本地 REST/SSE 控制台、历史 Run、计划/验证/指标面板与协作式取消；
- 独立只读 Explore Subagent，拥有自己的 Prompt、上下文、预算、child Run、Trace 和结构化证据报告；
- `delegate_explore` 父子委派、两次上限、失败降级和 Patch 前证据复核门禁；
- 37 个离线测试、5 个 M0 问答、3 个 M1 和 20 个 M3 Coding 评测任务。

M3 **仍未实现**操作系统级进程沙箱和人工审批队列；仓库测试代码仍以当前用户权限运行。边界见 [威胁模型](docs/threat-model.md)，架构和协议见 [架构](docs/architecture.md) 与 [事件协议](docs/event-protocol.md)。

## 环境

- Python 3.11+；
- Windows、macOS 或 Linux；
- DeepSeek API Key（离线自动测试不需要）；
- 推荐安装 `rg`（ripgrep）；没有时会使用较慢的 Python 搜索回退。

## 启动（零依赖）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe run_mini_code.py --help
```

macOS/Linux 将第二条替换为：

```bash
./.venv/bin/python run_mini_code.py --help
```

### Windows 推荐：简易启动脚本

日常使用不需要记住完整 Python 命令：

```powershell
.\start.cmd
```

脚本默认进入连续对话。也可以选择其他命令：

```powershell
.\start.cmd chat
.\start.cmd run "修复 calculator.py 的除零 Bug，并运行测试"
.\start.cmd resume <run_id>
.\start.cmd status <run_id>
.\start.cmd replay <run_id>
.\start.cmd security
.\start.cmd explore "定位配置加载流程并给出代码证据"
.\start.cmd webdemo
.\start.cmd demo
.\start.cmd demo2
.\start.cmd ask "这个项目的入口在哪里？请引用代码行"
.\start.cmd test
.\start.cmd eval
.\start.cmd eval1
.\start.cmd eval3 20
.\start.cmd trace <run_id>
.\start.cmd help
```

`start.cmd` 只为本次启动绕过 PowerShell 脚本执行策略，不会修改系统设置。它会自动使用 `.venv\Scripts\python.exe`、项目根目录和根目录 `.env`。

## 配置 DeepSeek

在项目根目录复制配置模板：

```powershell
Copy-Item .env.example .env
notepad .env
```

填写：

```dotenv
DEEPSEEK_API_KEY=你的真实Key
MINICODE_BASE_URL=https://api.deepseek.com
MINICODE_MODEL=deepseek-v4-flash
```

Key 只放在 `.env`；该文件已加入 `.gitignore`，Agent 工具也禁止读取它。

## 运行

Windows 推荐直接使用：

```powershell
.\start.cmd
```

进入后可以连续追问：

```text
MiniCode> M0 的三个只读工具分别在哪里实现？
MiniCode> 刚才提到的搜索工具如何限制结果数量？
MiniCode> /history
MiniCode> /clear
MiniCode> /exit
```

只读 `chat` 的上下文仍只在当前进程中存在；M2 的持久化与压缩目前只接入 Coding `run`，聊天持久化留作后续扩展。

### M1 修改任务

对当前项目执行 Coding Agent：

```powershell
.\start.cmd run "修复指定 Bug，并补充或更新测试"
```

第一次体验建议运行一次性 Demo。它会把故障样例复制到 `.mini-code/demos/` 后再修改，不会改动模板：

```powershell
.\start.cmd demo
```

分析其他 Git 仓库时使用底层命令：

```powershell
.\.venv\Scripts\python.exe run_mini_code.py run `
  "修复除数为零时的异常并运行测试" `
  --workspace D:\path\to\repo `
  --env-file D:\000Study\agentProgram\.env
```

M1 的三题真实模型评测：

```powershell
.\start.cmd eval1
```

评测使用不可由 Agent 访问的 grader 测试副本，报告保存在 `.mini-code/evals/m1-*/report.json`。

### M2 checkpoint 与恢复

普通 `run` 会在初始状态、每个工具步骤、上下文压缩和结束状态保存 checkpoint。若运行被中断：

```powershell
.\start.cmd status <run_id>
.\start.cmd resume <run_id>
```

第一次验收建议使用可恢复 Demo，它会降低上下文阈值并在第 6 步按预算暂停：

```powershell
.\start.cmd demo2
```

记下输出的 Demo workspace 和 Run ID，然后执行：

```powershell
.\start.cmd status <run_id> "<Demo workspace>"
.\start.cmd resume <run_id> "<Demo workspace>"
.\start.cmd trace <run_id> "<Demo workspace>"
```

持久化目录：

```text
.mini-code/
├─ state.db
└─ runs/<run_id>/
   ├─ events.jsonl
   ├─ checkpoints/*.json
   └─ artifacts/*.txt
```

`MINICODE_MAX_CONTEXT_CHARS` 和 `MINICODE_ARTIFACT_THRESHOLD` 可调整压缩与 artifact 阈值。恢复时若源码与最新 checkpoint 的工作区指纹不同，Agent 会停止并报告冲突，不会在过期上下文上继续 Patch。

### M3 Replay、安全审计与完整评测

```powershell
.\start.cmd replay <run_id> "<运行所用 workspace>"
.\start.cmd security
.\start.cmd eval3 20
```

`security` 和 `test` 完全离线；`eval3` 会真实调用 DeepSeek 20 题，消耗 Token，建议先运行 `.\start.cmd eval3 1`。报告位置和指标定义见 [M3 评测说明](docs/evaluation.md)。如需成本估算，在 `.env` 按供应商当前价格填写 `MINICODE_INPUT_COST_PER_MILLION` 与 `MINICODE_OUTPUT_COST_PER_MILLION`；报告会保存该价格快照。

### M4 本地 Web 控制台

第一次请使用隔离 Demo：

```powershell
.\start.cmd webdemo
```

浏览器打开 `http://127.0.0.1:8765`。普通 `.\start.cmd web` 会把项目根目录作为目标 workspace，不建议拿它做修改型试验。完整通过标准见 [M1–M4 验收指南](docs/M1-M4验收指南.md)。

### M5.1 Explore Subagent

```powershell
.\start.cmd explore "定位 M4 Web 服务入口和安全边界，请返回路径与行号"
```

M5.1 的 child 仍可独立运行；M5.2 已让主 CodingAgent 通过 `delegate_explore` 自动创建它。详见 [M5 验收指南](docs/M5验收指南.md)。

M5.2 已在普通 `run`、`demo`、`resume` 和 Web Coding Run 中默认启用。单 Agent 对照可使用底层参数 `run --no-subagents`。父子 Replay 聚合与 Web Subagent 卡片属于 M5.3。

以下是等价的底层完整命令，便于理解和跨平台使用。

分析 MiniCode 自己：

```powershell
.\.venv\Scripts\python.exe run_mini_code.py ask "M0 有哪些只读工具？请引用代码行" --workspace .
```

分析其他仓库：

```powershell
.\.venv\Scripts\python.exe run_mini_code.py ask "这个项目的程序入口在哪里？" --workspace D:\path\to\repo --env-file D:\000Study\agentProgram\.env
```

注意：默认 `.env` 相对于当前终端目录，而不是 `--workspace`。

## 查看 Trace

运行结束会打印 `Run ID`：

```powershell
.\.venv\Scripts\python.exe run_mini_code.py trace <run_id> --workspace .
```

原始事件位于：

```text
.mini-code/runs/<run_id>/events.jsonl
```

## 自动测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

自动测试使用 Fake Provider，不访问网络、不消耗 Token。

## M0 真实模型评测

```powershell
.\.venv\Scripts\python.exe run_mini_code.py eval-m0 --workspace .
```

它会让 DeepSeek 完成 5 个仓库分析任务，并将结果写入：

```text
.mini-code/evals/<suite_id>/report.json
```

完整人工验收步骤和通过标准见 [docs/验收手册.md](docs/验收手册.md)。

## 项目结构

```text
src/mini_code/
├─ agent.py          # Agent Loop 与流式工具调用组装
├─ cli.py            # run / demo / chat / ask / trace / eval
├─ config.py         # .env 与 DeepSeek 配置
├─ events.py         # 事件协议与 JSONL Trace
├─ provider.py       # DeepSeek 流式 API 客户端
├─ workspace.py      # 路径边界与仓库地图
├─ eval_m1.py        # 三题真实模型 Coding 评测
├─ eval_m3.py        # 20 题隐藏测试评测与报告
├─ context.py        # M2 上下文压缩和 artifact 摘要
├─ persistence.py    # SQLite、JSON checkpoint 与冲突检测
├─ replay.py         # M3 Trace 重放和运行指标
├─ security.py       # M3 版本化安全探针
├─ web.py            # M4 本地 REST/SSE 服务与 Run 管理
├─ web_static/       # M4 零依赖控制台页面
├─ subagents.py      # M5 Explore Subagent 与结构化报告协议
└─ tools/
   ├─ readonly.py    # M0 三个只读工具
   └─ coding.py      # M1 Patch、进程、计划和完成协议
```
