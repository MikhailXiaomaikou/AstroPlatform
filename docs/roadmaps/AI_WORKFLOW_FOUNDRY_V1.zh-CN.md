# Standard Astro：AI 科研工作流工厂 v1——候选 Demo 记录版

## 目标与交付边界

AI 可以发现能力缺口、设计候选工具、编写代码和测试，并在隔离环境运行可保存的 Demo。系统严格区分两条序列：

```text
候选序列：能力缺口 → AI 候选 → 自动 Demo → 不可变记录 → 等待审核
正式序列：人工批准 → 签名镜像与注册表 → 正式 Audit → 独立复算 → 结果审核 → SUPPORTED
```

正式序列从 `REGISTERED` 开始。候选 Demo 即使运行成功，也只能标记为 `NON_FORMAL_DEMO`，不得进入正式模型工具列表、普通 Claim Audit、正式 Evidence Pack 或 `SUPPORTED` 结论。

工程目标是 48 小时内记录首个候选 Demo、96 小时内完成工程集成，随后进行不能压缩的 72 小时观察。若人工审核尚未完成，状态必须写成 `engineering complete; formal registration pending human review`。

## 候选目录与正式注册表

Candidate Catalog 保存成功、失败和被拒绝的候选。生命周期为：

```text
DRAFT → BUILDING → VALIDATING → DEMO_RECORDED → REVIEW_PENDING
      → APPROVED → PROMOTED
      → REJECTED
```

每次候选内容变化都创建新版本，不覆盖旧版本。每个 Demo 至少记录：候选与 WorkflowSpec 哈希、代码 tree/patch、依赖 lock、SBOM、fixture 与数据哈希、生成模型与配置、runner digest、起止时间、日志哈希、结构化结果、失败分类和资源使用。

候选输出固定包含：

```text
candidate_id
candidate_version
demo_run_id
status=PASSED|PARTIAL|FAILED
evidence_class=NON_FORMAL_DEMO
publication_ready=false
claim_eligible=false
limitations
validation_summary
```

大型日志和临时产物保留 30 天；结构化报告、哈希、事件和审核记录持续保留。正式晋升不修改候选，而是把准确的候选版本哈希、验证回执、审核记录和签名镜像 digest 写入新的 Formal Registry entry。

## 工具与工作流标准

扩展优先级固定为：复用现有 WorkflowSpec → 组合现有 ToolSpec → 增加数据适配器 → 最后才增加科学算法。

`ToolSpec` 描述一个原子能力：版本化输入输出 Schema、固定 `entrypoint_id`、执行/存储/网络/资源策略、provenance 与 claim policy、代码和依赖摘要。

`WorkflowSpec` 描述一份实验配方：来源与主张范围、固定数据和论文哈希、精确 ToolSpec DAG、主执行器、独立验证器、模型与统计方法、运行前冻结的阈值、允许/禁止表述、风险等级与 Evidence Pack 要求。

`RegistrySnapshot` 是带 epoch、内容哈希、镜像/SBOM/验证摘要、审核记录、签名、替代和撤销状态的不可变正式目录。生产环境只运行签名快照中的固定 `entrypoint_id`，不接受 manifest 中的任意源码、shell、Python、模块路径或下载 URL。

风险门：R0 格式/哈希/单位需要自动测试和人审；R1 来源/读取器需要 fixture 校准和人审；R2 表格/chain 区间需要独立检查和科学审核；R3 likelihood/拟合/tension 需要独立实现、工程审核和科学审核；R4 新发现和自动发表不属于 v1。

工作流注册审核与科研结果审核相互独立。正式 R2/R3 工作流的每次运行仍需独立复算、结果人工审核和确定性 Finalizer 才能得到 `SUPPORTED`。

## 模型、Worker 与隔离边界

模型只获得两个稳定入口：

```text
discover_registered_workflows
start_registered_workflow
```

它们只读取 Formal Registry。调用只传 `workflow_id`、`workspace_id`、`source_id` 和 `candidate_id`；服务器重新绑定版本、数据 pins、参数和阈值。

Worker v2 信封增加 `workflow_version`、`registry_epoch`、`registry_entry_hash`、`entrypoint_id` 和 `worker_image_digest`。本地 Worker 只执行正式签名工作流，候选 Demo 只在一次性 Validation Runner 运行。Worker capability 由其镜像内的签名 RegistrySnapshot 生成；不匹配时返回 `worker_upgrade_required`。

Draft Job 可以调用 AI 但不执行候选代码。Validation Job 可以执行候选代码但没有 AI、生产 DB、Redis、S3、用户数据或签名密钥。Runner 必须非 root、只读根、默认无网络、不挂载 Home/Docker socket，并限制 CPU、内存、进程、时间与输出。候选 CI 不得使用 `pull_request_target`。

Registry、Worker task、Evidence Pack 和镜像签名身份必须分离。暂停或撤销发生后，系统在任务签发、结果接收和 Finalizer 提交前重新检查状态；运行中的结果保持 `WITHHELD`。

## 数据、API 与界面

新增持久记录：

```text
capability_requests
foundry_candidates
foundry_candidate_versions
foundry_demo_runs
foundry_validation_runs
foundry_candidate_events
foundry_reviews
workflow_registry_entries
workflow_registry_releases
```

新 Audit、ResearchJob 和 ScienceExecutionAttempt 保存精确的 workflow id/version、registry epoch/entry hash、entrypoint 和 runner digest。旧 Evidence Pack 不重写；新 Pack 增加 Registry 与 runner 绑定。

用户 API：

```text
GET  /api/research/workflows
POST /api/research/claim-audits/{audit_id}/capability-requests
GET  /api/research/capability-requests
GET  /api/research/capability-requests/{request_id}
GET  /api/research/foundry-candidates/{candidate_id}
GET  /api/research/foundry-candidates/{candidate_id}/demo-runs
```

管理员 API 覆盖 request triage/merge、candidate validate/review/register/suspend/revoke。AI 身份没有这些治理权限。浏览器只能提交服务器已有的 `gap_id`，不能上传代码、哈希或阈值。

Research Workspace 改为服务端 Workflow Catalog，显示结构化 CAPABILITY_GAP、Foundry 申请、候选构建与 Demo 状态，以及正式注册后的 Audit revision。Foundry Console 提供 Gap Inbox、Candidate Versions、Demo Runs、Validation、Reviews、Formal Registry 和 Revocations。候选使用独立 URL、颜色和数据类型，并固定显示“候选 / Candidate”“非正式 / Non-formal”“不能支持科研结论”。新增界面完整支持中英双语。

## AI 并行实施顺序

```text
0–4h    冻结工具/科学输出基线与公共 Schema
4–20h   并行实现 Registry、Candidate Catalog、API 和 UI
20–36h  Worker v2、稳定模型入口、Draft/Validation Job、不可变 Demo ledger
36–48h  生成并记录 DESI DR2 官方 chain 完整性与区间摘要候选 Demo
48–72h  审核、签名、注册、撤销与 Evidence Pack 绑定
72–96h  全量测试、多架构镜像、真实 E2E 与 dark deployment
第4–7天 72 小时观察后才开启正式注册
```

功能开关按 Registry shadow → Gap tracking → Candidate Catalog → AI drafting → 自动记录 Demo → 人工注册的顺序启用：

```text
WORKFLOW_REGISTRY_V2_ENABLED=false
FOUNDRY_GAP_TRACKING_ENABLED=false
FOUNDRY_AI_DRAFTING_ENABLED=false
FOUNDRY_AUTO_DEMO_ENABLED=false
FOUNDRY_CANDIDATE_CATALOG_ENABLED=false
FOUNDRY_REGISTRATION_ENABLED=false
```

## 验收

- Union3 迁移后科学结果不变，DESI DR2 进入统一 Registry。
- 新组合工作流不需要手工修改前端、Worker、模型 manifest 或主 dispatcher。
- 48 小时内完成一个可在服务重启后恢复的 `DEMO_RECORDED` 候选。
- Candidate version、Demo Run、event 和 review 都不可覆盖。
- 修改代码、依赖、数据、fixture 或阈值后，旧验证和审核不能复用。
- Candidate Catalog 中的工作流不能被正式模型或 Worker 调用。
- Demo 永远不能产生 `SUPPORTED` 或正式 Evidence Pack。
- 正式 Registry 准确绑定获批 candidate version hash；AI 不能自审、自签或注册。
- 被暂停或撤销的工作流不能获得新 lease 或通过 Finalizer。
- 完成 `CAPABILITY_GAP → 请求 → AI Candidate → DEMO_RECORDED → 人审 → REGISTERED → Audit revision → 本地 Worker → 独立复算 → 结果审核 → SUPPORTED` 的真实闭环。
- `unsupported scientific escape`、`unsigned registration`、`AI self-approval`、`candidate-to-formal bypass` 和 `sandbox escape` 均为 0。

