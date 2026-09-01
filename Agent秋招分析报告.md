# 2026 Agent 技术与秋招竞争力分析报告

> 面向：2027 届、研三、目标为后端开发 / AI Agent 开发岗位  
> 调研截止：2026-08-29（Asia/Shanghai）  
> 结论基于公开资料和招聘样本，不把招聘平台的宣传数字视为精确的全市场统计。

## 0. 先说结论

对秋招最有用的定位不是“纯大模型算法研究员”，也不是“只会调用模型 API”，而是：

> **后端工程基础扎实、能把 LLM 接进真实业务、能用评测和可观测性证明系统可靠的 Agent 工程候选人。**

建议能力投入优先级：

1. **后端基本功**：Python/Java/Go 至少精通一门，数据结构、操作系统、网络、数据库、缓存、异步任务、Linux、Docker。
2. **Agent 核心闭环**：模型决策 → 工具调用 → 结果观察 → 状态更新 → 终止；理解失败、重试、预算和幂等。
3. **上下文工程**：结构化输出、RAG、会话状态、短期/长期记忆、信息裁剪，而不只是“Prompt 技巧”。
4. **可靠性工程**：离线评测、回归集、轨迹评测、Trace、延迟/成本统计、失败分类。
5. **安全与权限**：工具最小权限、危险操作审批、提示注入防护、敏感信息与记忆污染防护。
6. **协议与生态**：会用 Function Calling 和 MCP，知道 A2A 解决的边界；不必为了“前沿”强行做多 Agent。

对个人项目而言，一个规模适中、能真实使用、带评测数据和故障案例的单 Agent，通常比“五个角色互相聊天”的 Demo 更能证明工程能力。

---

## 1. 本次调研如何做

资料分为四层：

- **一手技术资料**：OpenAI Agents SDK、MCP、A2A、LangGraph、Google Gemini、Microsoft AutoGen 等官方文档。
- **安全与评测资料**：NIST、OWASP、官方 Evals 文档和 Agent 评测综述。
- **市场资料**：2025—2026 年中国 AI 人才报告与公开招聘统计。
- **岗位样本**：2026—2027 届 Agent 校招岗位、企业官网岗位及高校就业网职位。

阅读招聘数据时应注意选择偏差：单个平台不等于整个市场；社招高薪不能直接外推到应届生；“AI 人才缺口”不等于任意 AI 项目都容易拿 Offer。

---

## 2. 什么才算 Agent

OpenAI 的定义强调两个条件：LLM 能管理工作流、决定何时结束或纠错；系统能动态选择工具，在约束内获取信息或执行动作。单轮问答、情感分类器、固定链式 Prompt 不因此自动成为 Agent。[OpenAI：A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)

Anthropic 将 Agent 与 Workflow 区分：Workflow 由代码预先规定路径，Agent 由模型动态决定过程和工具使用。工程上二者不是二选一，而应混合使用：确定性的权限、校验和业务规则写在代码里；真正含糊、需要语义判断的部分交给模型。[Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

一个最小但完整的 Agent 可以抽象为：

```text
用户目标
  ↓
状态/上下文 → 模型决策 → 工具调用 → 观察结果
     ↑                            ↓
     └──── 更新状态、检查预算与终止条件 ────┘
```

“完整”不取决于 Agent 数量，而取决于它是否具备：

- 明确目标和状态；
- 可验证、带 Schema 的工具；
- 有上限的循环与明确终止条件；
- 错误恢复和幂等语义；
- 人类可见的执行轨迹；
- 可重复的质量评测。

---

## 3. 2026 年 Agent 技术版图

### 3.1 从 Prompt Engineering 转向 Context Engineering

Prompt 仍重要，但生产问题更多来自“给了模型什么、何时给、给多少、来源是否可信”。上下文工程至少包括：

- 系统指令、用户目标和业务规则的优先级；
- 工具描述与 JSON Schema；
- 检索到的文档和来源元数据；
- 当前任务状态、计划、最近轨迹；
- 用户长期事实、偏好和历史结论；
- Token 预算、裁剪、压缩和缓存策略。

OpenAI Agents SDK 已把 sessions、handoffs、guardrails、structured outputs、MCP 和 tracing 作为一等能力，而不是只提供一次模型调用。[OpenAI Agents SDK：Agents](https://openai.github.io/openai-agents-python/agents/) [Sessions](https://openai.github.io/openai-agents-python/sessions/)

面试中应能解释三类记忆：

| 层次 | 示例 | 典型实现 | 风险 |
| --- | --- | --- | --- |
| 工作记忆 | 当前 JD、当前步骤、工具结果 | Agent state / checkpoint | 上下文膨胀、状态不一致 |
| 情景记忆 | 某次模拟面试表现、历史任务轨迹 | PostgreSQL / event log | 错误内容长期保留 |
| 语义记忆 | 用户技能、项目证据、偏好 | 结构化表 + 向量检索 | 过期、冲突、记忆污染 |

不要把“把全部聊天记录塞回模型”称作完善的 Memory。

### 3.2 工具调用已标准化，但执行仍是应用的责任

Function Calling 的核心是：模型返回结构化的工具名和参数，应用校验并执行，再将结果回传。Google 的官方文档也明确，自定义函数的实际执行责任属于应用，并支持并行和组合调用。[Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)

工程上要处理的远多于一个装饰器：

- 参数 Schema 与运行时二次校验；
- 超时、重试、熔断和退避；
- 幂等键，避免重试导致重复写入；
- 读工具与写工具分级；
- 用户身份、租户和权限透传；
- 大结果落盘、摘要和可引用来源；
- 工具版本、错误码和可观测字段；
- 并行调用时的依赖与竞态。

这也是后端背景的候选人在 Agent 岗上的明显优势。

### 3.3 MCP 成为工具/上下文接入层，A2A 面向 Agent 间互操作

MCP 采用 host-client-server 架构，基于 JSON-RPC 暴露 resources、tools 和 prompts；服务器彼此隔离，完整对话历史由 host 控制。[MCP Architecture](https://modelcontextprotocol.io/specification/2026-07-28/architecture)

截至调研日，MCP 最新核心规范是 `2026-07-28`。它相对 `2025-11-25` 有较大变更：核心协议改为无状态、每次请求携带版本与客户端能力，引入 `server/discover`，并把实验性 Tasks 移到官方扩展；同时规定了 OpenTelemetry trace context 的传播方式。[MCP 2026-07-28 Key Changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog)

A2A 最新规范已进入 v1.0，解决独立、可能互不透明的 Agent 系统之间的能力发现、任务协作与安全交换，不要求彼此暴露内部状态、记忆或工具。v1.0 增加了更明确的版本兼容、Agent Card 签名、多租户、REST/gRPC/JSON-RPC 等价绑定和现代 OAuth 流程。[A2A Protocol v1.0](https://a2a-protocol.org/latest/specification/) [v1.0 变更说明](https://a2a-protocol.org/latest/whats-new-v1/)

面试时可用一句话区分：

> **MCP 更像 Agent 连接工具和数据的标准插座；A2A 更像独立 Agent 服务之间的协作协议。**

个人项目应优先实现一个小型 MCP server 或 client；A2A 可作为扩展设计，不必为展示概念增加部署复杂度。

### 3.4 编排：先单 Agent，再考虑多 Agent

OpenAI Agents SDK 总结了两种常见多 Agent 模式：manager 把专家当工具调用，或 handoff 将当前会话控制权移交给专家。[OpenAI：Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)

框架只是控制流表达方式：

| 选择 | 强项 | 适合秋招项目的用法 | 主要风险 |
| --- | --- | --- | --- |
| 显式自研 Loop | 原理透明、便于讲解 | 实现最小循环、预算、事件流 | 可靠性组件需自己补齐 |
| LangGraph | checkpoint、持久执行、HITL | 复杂状态机、暂停/恢复 | 容易只会“拼节点” |
| OpenAI Agents SDK | tools、handoffs、guardrails、sessions、tracing 集成 | 快速做标准 Agent | 需说明供应商边界 |
| Google ADK | 工作流/Agent、多语言与 Google 生态 | 企业集成型项目 | 项目生态选择成本 |
| AutoGen | 事件驱动、多 Agent runtime | 多 Agent 研究或分布式实验 | 对简单项目偏重 |

LangGraph 的定位是 durable execution、streaming、human-in-the-loop 等底层编排能力，其 persistence 还支持故障恢复和 time-travel debugging。[LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview) [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

推荐做法：用少量代码实现一版 Loop 证明理解，再在主应用中使用显式状态图；不要同时堆叠三四个 Agent 框架。

### 3.5 可靠性：Eval 与 Trace 已经是核心功能

2025 年 Agent 评测综述指出，评测已从单一最终答案扩展到规划、工具使用、反思、记忆、成本效率、安全和鲁棒性，并正在转向更真实且持续更新的环境。[Survey on Evaluation of LLM-based Agents](https://arxiv.org/abs/2503.16416)

Agent 评测应分四层：

| 层次 | 问题 | 示例指标 |
| --- | --- | --- |
| 确定性单元测试 | 工具本身对不对 | Schema、过滤、排序、权限、幂等 |
| 轨迹评测 | 过程是否合理 | 工具选择准确率、非法调用率、平均步数 |
| 结果评测 | 任务是否完成 | task success、字段完整性、引用准确率 |
| 系统评测 | 能否上线 | P50/P95 延迟、成本、错误率、恢复率 |

OpenAI 的 Evals 支持数据源、grader 和重复运行；Agents SDK tracing 默认记录模型生成、工具调用、handoff 和 guardrail 等事件。[OpenAI Evals](https://platform.openai.com/docs/api-reference/evals) [Agents SDK Tracing](https://openai.github.io/openai-agents-python/tracing/)

作品集里最有说服力的不是“效果很好”，而是类似：

```text
评测集：80 条（正常 50 / 边界 15 / 注入与越权 15）
任务成功率：基线 61.3% → 当前 82.5%
错误工具调用率：12.0% → 3.8%
P95：8.4 s；单任务平均成本：¥0.xx
仍失败：跨 JD 同义技能归并、证据冲突、超长 PDF
```

数字必须由项目真实运行产生，禁止先写结论再找数据。

### 3.6 安全：工具越强，传统后端安全越重要

NIST 指出，间接提示注入可把网页、邮件、文档里的恶意指令带入 Agent，导致其执行攻击者指定的任务。[NIST：AI Agent Hijacking Evaluations](https://www.nist.gov/news-events/news/2025/01/technical-blog-strengthening-ai-agent-hijacking-evaluations)

OWASP 2026 Agentic Top 10 覆盖目标劫持、工具滥用、身份/权限滥用、记忆污染、不安全的 Agent 通信、级联故障等风险。[OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

MCP 当前授权规范以 OAuth 2.1 为基础，要求 Protected Resource Metadata、`resource` 参数、PKCE/issuer 校验和最小 scope 等机制；Dynamic Client Registration 已被标为兼容性保留，推荐转向 Client ID Metadata Documents。[MCP 2026-07-28 Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

个人项目至少做到：

- 外部文档一律标为 untrusted data，不允许覆盖系统目标；
- 读/写工具分权，写操作默认需要审批；
- 工具白名单、路径/域名 allowlist、参数校验；
- 密钥不进 Prompt、不进 Trace；
- 记忆写入需验证来源，支持查看、纠正、删除；
- 限制最大步数、Token、金额和墙钟时间；
- 保存审计轨迹，但对个人信息脱敏。

---

## 4. 中国秋招市场：岗位到底在要什么

### 4.1 大盘有机会，但竞争也在同时上升

猎聘 2025 报告显示，AI 技术职位中明确要求硕博的比例为 46.98%，显著高于整体职位；对研三学生是学历窗口，也是工程/项目能力门槛。[猎聘 2025 AI 技术人才供需洞察](https://data.eastmoney.com/report/zw_industry.jshtml?infocode=AP202503061644099941)

中国劳动和社会保障科学研究院发布的《中国人工智能人才发展报告（2025—2026）》称，AI 人才集中于京津冀、长三角、粤港澳大湾区，算法、大模型等前沿领域存在结构性紧缺。[发布摘要](https://www.calss.net.cn/p1/zkcg/20260423/45235.html)

脉脉公开数据称，2025 年 1—7 月 AI 新发岗位量和简历投递量都大幅增长。它说明“需求热”和“求职者涌入”同时存在，不能只看岗位增幅。[中国日报转述《2025 年 AI 人才流动报告》](https://cn.chinadaily.com.cn/a/202509/18/WS68cbb97aa310f072577492b3.html)

### 4.2 当前岗位样本的共同要求

2026—2027 校招样本显示：

- 合合信息 Agent 开发岗把任务规划、上下文、记忆、工具调用、RAG、MCP、评测、调试、延迟、稳定性和成本放在同一岗位描述中；同时要求 Git、Linux、HTTP、数据库和后端基础。[合合信息 2027 校招样本](https://www.shushuqiuzhi.com/position/435773)
- 映翰通校招岗要求 Python/Java、计算机基础、RESTful API、数据库、Git，并点名 LangChain、LangGraph、LlamaIndex。[高校就业网岗位页](https://myjob.dlmu.edu.cn/campus/view/id/865583)
- 蚂蚁财富 AI Lab 的校招描述进一步要求长链路、多工具、可追踪/可验证，以及任务成功率、工具准确率、延迟、成本、失败率、异常类型和链路追踪等指标。[蚂蚁 Agent 工程校招样本](https://jobs.ultraai.site/jobs/ant_campus/26042809805650)
- 京东 AI 应用岗位把权限控制、失败兜底、离线评测、回归测试、人工反馈、Trace、缓存、异步任务、容器化和云服务列为落地能力。[京东招聘官网岗位](https://zhaopin.jd.com/web/job-info-detail?requementId=220736)
- 上海人工智能实验室校园岗位强调外部工具、可扩展 Agent 系统与自动化评测。[上海 AI Lab 校招岗位](https://www.shlab.org.cn/joinus/detail/7585178768231909686?mode=7)

由此可将岗位能力归纳成五组：

| 能力组 | 高频要求 | 简历应提供的证据 |
| --- | --- | --- |
| 软件基础 | 一门主语言、DSA、OS、网络、Linux、Git | 代码质量、测试、性能分析、面试题准备 |
| 后端工程 | API、DB、缓存、异步、微服务/容器 | 可部署服务、表结构、幂等与故障恢复 |
| LLM 应用 | RAG、Prompt/Context、Embedding、结构化输出 | 可解释方案和对照实验 |
| Agent | planning、tool use、memory、workflow、MCP | 状态图、工具协议、暂停恢复、真实轨迹 |
| 生产保障 | eval、trace、成本、延迟、安全 | 数据集、Dashboard/日志、指标与失败复盘 |

### 4.3 后端岗与 Agent 岗如何同时准备

两类岗位的交集很大，但面试重心不同：

| 维度 | 后端岗 | Agent 岗 | 共同准备方式 |
| --- | --- | --- | --- |
| 主语言 | Java/Go/Python 深度 | Python 常见，也接受 Java/Go | 选一门主语言讲到底层与工程实践 |
| 系统设计 | 高并发、高可用、存储、消息 | 状态、工具、上下文、模型服务 | 用同一个项目讲 API、DB、队列、Agent |
| 算法题 | 通常权重高 | 仍常考，不会消失 | 每天稳定练习，不因做 Agent 停掉 |
| AI 原理 | 加分或岗位相关 | Transformer、推理、RAG、评测必问 | 能解释机制和取舍，不必重训大模型 |
| 项目追问 | QPS、事务、缓存、故障 | 幻觉、工具错调、成本、评测 | 准备两套项目叙事，数据保持一致 |

推荐投递组合：

- 主投：AI 应用开发、Agent 工程、LLM 应用后端、智能平台研发；
- 并投：Python/Java/Go 后端、平台研发、中间件/工程效能；
- 谨慎投入：明确要求顶会、预训练、RLHF/RL infra 的纯算法研究岗，除非已有对应论文或训练经验。

---

## 5. 现有 `codingAgent.md` 的审计结论

现有文档有教学价值，但不能作为 2026 年行业报告直接使用。

### 可保留的核心

- Agent Loop、工具注册、流式事件、上下文、planning 是正确学习主线；
- `max_steps`、指数退避、工具消息配对等是有价值的工程点；
- 自研最小 Loop 有助于面试时解释底层机制。

### 必须校正的问题

1. **文档不完整**：目录有 10 章，文件实际在第 6 章代码块后结束，Memory、Eval、工程化和总结并未写出。
2. **缺少来源**：对 Claude Code、Codex、Hermes 内部实现的细节描述没有仓库版本、commit 或官方链接，不宜当作事实引用。
3. **型号可疑**：`deepseek-v4-flash` 未在文档中给出官方出处；模型名、上下文窗口和 API 形态都是易变信息，应进入配置而非教程结论。
4. **技术接口偏旧**：示例以 `chat.completions.create` 手动拼工具调用为中心；这仍可用于理解原理，但当前工程还应关注 Responses/Agents SDK、会话、Tracing、MCP 和内建 Guardrails。
5. **Token 估算过粗**：`4 字符 ≈ 1 token` 对中英文混合并不可靠，应优先使用模型 tokenizer 或 API usage。
6. **压缩策略有信息完整性风险**：简单丢弃旧消息可能丢掉约束、决策和未完成任务；应把“状态”从聊天文本中分离并结构化持久化。
7. **安全边界不足**：任意路径 `read_file/write_file`、字符串参数直接执行、异常文本原样回传都不适合作为生产示例。
8. **评测缺席**：文档声称有 eval 目录但正文和当前仓库均无代码，无法验证“可运行项目”。

因此，新项目不继续补写这份“仿某产品内部架构”的长教程，而以可运行、可评测、能解释取舍的业务 Agent 为中心。

---

## 6. 为本项目选择的方向

项目暂定名：**Mini Claude Code——可恢复、可回放的 Coding Agent**。

它接收自然语言编程任务，在一个受控工作区内自主完成：

> 理解需求 → 探索仓库 → 制订计划 → 修改文件 → 执行测试 → 根据结果继续修复 → 输出 Diff 与验证报告。

项目不强行绑定后端、运维或求职业务。它本身就是对 Agent 核心能力的集中展示：循环决策、工具调用、上下文管理、规划、文件编辑、进程执行、安全边界、错误恢复和评测。

为了避免成为只有 `while + function calling` 的普通 Demo，特色放在运行时机制上：

- **验证优先**：Agent 不能仅凭“感觉”宣布完成，必须给出测试、静态检查或可解释的验证证据。
- **Checkpoint 与恢复**：每一步持久化状态；模型或工具中断后可从最近安全点继续，而不是重新消耗整段上下文。
- **轨迹回放**：保存模型决策摘要、工具参数、结果、Diff、Token 和耗时，失败任务可以复盘。
- **渐进式上下文**：先看仓库地图和搜索结果，再按需读取文件；大工具输出落盘并返回引用，避免把整个仓库塞进上下文。
- **安全工具层**：限制工作区路径和命令，区分只读/写入/执行工具，对高风险操作要求确认。
- **可重复评测**：用固定小仓库和任务集衡量任务成功率、测试通过率、无关修改率、成本和危险操作拦截率。

详细范围、架构和六周实施安排见 `Agent项目计划表.md`。

---

## 7. 学习与面试策略

### 必须能手写/讲清

- 一个带最大步数、超时、重试和终止判断的 Agent Loop；
- Function Calling 的完整消息流和 Schema 校验；
- RAG 的切分、召回、重排、引用与评测；
- SQL 索引、事务隔离、Redis 缓存/分布式锁、消息队列基本语义；
- SSE/WebSocket 流式接口；
- Prompt injection、最小权限、人类审批；
- task success、trajectory、latency、cost 的评测设计。

### 会使用并能说明取舍

- LangGraph 或同类状态图框架；
- PostgreSQL + pgvector，Redis，FastAPI，Docker Compose；
- OpenTelemetry/结构化日志或一个 Agent tracing 方案；
- MCP server/client；
- 至少两个模型供应商的适配与降级。

### 先不重投入

- 为展示概念硬做大规模多 Agent；
- 没有算力和数据却做“从零训练大模型”；
- 只堆 Dify 页面、没有代码和评测；
- 过早上 Kubernetes、复杂微服务或完整前端设计系统；
- 每周追新框架，反复重写项目。

---

## 8. 可执行的成功标准

在秋招投递前，应至少形成以下证据：

- 1 个公开或可演示的 Agent 项目，README 可在 5 分钟内跑通；
- 1 张架构图、1 张 Agent 状态图、1 份 API 文档；
- 50—100 条版本化评测集，含正常、边界和安全样例；
- 一份真实评测报告，列出基线、改进、成本和剩余失败；
- 3 个可讲 10 分钟的故障故事：问题、定位、权衡、修复、数据；
- 2 套项目表述：面向后端岗强调可靠服务，面向 Agent 岗强调上下文与评测闭环；
- 算法题、八股和项目三条线并行，不让项目吞掉全部准备时间。

---

## 9. 来源索引与更新规则

### 关键技术与标准

- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [MCP Specification](https://modelcontextprotocol.io/specification/)
- [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)
- [LangGraph Documentation](https://docs.langchain.com/oss/python/langgraph/overview)
- [Microsoft AutoGen Documentation](https://microsoft.github.io/autogen/stable/index.html)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [OWASP Agentic Applications Top 10](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

### 市场与岗位

- [中国人工智能人才发展报告（2025—2026）发布摘要](https://www.calss.net.cn/p1/zkcg/20260423/45235.html)
- [北京大学国发院：LLM 影响下的劳动力市场求职错配报告](https://nsd.pku.edu.cn/xzyj/cbw/yjbgxl/9295ba3c7e5c4a24aaf57798c94cb1a2.htm)
- [猎聘 2025 AI 技术人才供需洞察](https://data.eastmoney.com/report/zw_industry.jshtml?infocode=AP202503061644099941)

更新规则：框架版本、模型名、协议版本和招聘岗位会变化。每月只检查一次官方 changelog 和目标企业 JD；报告中的稳定原则保留，易变信息用日期和链接标注，避免把“最新”写死进代码。
