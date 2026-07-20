# Standard Astro：Render 控制壳 + 本地算力 + 首个科研闭环

状态：已批准，进入实施  
面向版本：`v0.6.0-alpha.1`  
首个注册工作流：`union3_flat_lcdm_sn_only_v1`

## 1. 用一句话说明

Render 继续承担网页、账号、任务账本、来源、审核和证据包；用户电脑承担耗时的科学计算。两端只通过带签名的 HTTPS 协议通信，用户电脑永远不接触生产数据库、Redis 或证据签名私钥。

首个闭环只复现一项已经发表的结果：Union3 论文 v4 的 Table 9 中，SNe-only、平直 ΛCDM 条件下的物质密度参数 Ωm 区间。它不是新发现，也不能用于测量 H0。

## 2. 本轮完成定义

```text
创建私有 Workspace
→ 固定读取 Union3 arXiv 2311.12098v4
→ 定位 Section 5.3 / printed page 58 / Table 9
→ 用户电脑运行注册的 profile-χ² 工作流
→ Render verification 队列用独立实现复算
→ 不同于任务所有者的科学审核者确认
→ 确定性 Finalizer 写入 SUPPORTED
→ 下载并离线验证 Ed25519 Evidence Pack v2
```

必须同时满足：

- 主计算、独立复算、论文对照和人工审核四道门全部通过；
- `reproduction_ready=true`，但 `publication_ready=false`；
- 任一道门失败都不能被人工强行覆盖；
- LLM 不能写 verdict、审核、哈希、签名或 finalized 状态；
- 所有新功能默认关闭，按测试结果逐项开启。

## 3. 部署边界

### Render 控制壳保留

- Frontend：登录、Workspace、Reader、运行状态和审核界面；
- Backend：账号、任务派发、来源、审核和 Evidence API；
- PostgreSQL 16：用户、任务、来源、attempt、证据和审核的权威账本；
- Redis：控制队列、nonce、防重放、短期通知；
- Control Worker：删除、清理、来源抓取、打包和小型独立复算；
- Beat：定时清理、失败任务恢复、Daily 和来源检查；
- S3/R2：来源快照、科学产物和 Evidence Pack 的权威对象存储。

Render 不消费 `science.short`、`science.heavy` 或 `cosmology.mcmc` 队列，不运行重型科研任务。Redis 不是权威数据库。

### 用户电脑负责

- 固定版本科学数据和缓存；
- 注册工作流的数值计算；
- 链或曲线后处理、诊断和绘图；
- 将签名任务的结果和产物哈希上传到控制中心。

第一版通过 Docker 支持 macOS ARM64、Linux AMD64 和 Windows Docker Desktop；非 root、只读根文件系统、不挂载 Home、SSH key 或 Docker socket，也不接受任意 Shell/Python。

## 4. 进程角色与启动合同

```text
APP_ROLE=api|migration|control_worker|beat|science_worker
SCIENCE_EXECUTION_BACKEND=celery|https_worker
```

- `api`：完整 HTTP、登录、隐私和管理员配置；
- `migration`：只加载数据库及 40 位 Git SHA；
- `control_worker`：加载 DB、Redis、S3、删除和 Evidence 配置，不要求邀请管理员密钥；
- `beat`：加载 DB、Redis 和版本身份；
- `science_worker`：只加载控制中心 URL、节点密钥和缓存目录。

Alembic 导入 SQLAlchemy metadata 时不得实例化完整生产 Settings。Backend、Worker 和 Beat 必须报告同一完整 Git SHA。

Render Worker 只消费：

```text
control,maintenance,verification
```

`ResearchJob.execution_backend` 使用：

```text
control_celery | https_worker | reference_verifier
```

同一真实任务只能有一个主执行后端。

## 5. 本地 Worker 协议

### 登记

```text
POST   /api/compute/v1/enrollments
POST   /api/compute/v1/nodes/enroll
GET    /api/compute/v1/nodes
DELETE /api/compute/v1/nodes/{node_id}
```

一次性 code 有效 10 分钟，数据库只保存哈希，使用后立即失效；每位 Alpha 用户最多三个活动节点。Worker 在本地生成 Ed25519 密钥对，服务端只保存公钥。

### 请求签名

每次请求签名以下规范字符串：

```text
HTTP method
path
timestamp
nonce
body_sha256
worker_id
protocol_version
```

允许最多五分钟时钟误差；nonce 在 Redis 保存十分钟，重复 nonce、撤销节点、错误 owner 或错误协议版本都 fail closed。

### Lease 与结果

```text
POST /api/compute/v1/tasks/claim
PUT  /api/compute/v1/attempts/{attempt_id}/heartbeat
POST /api/compute/v1/attempts/{attempt_id}/complete
POST /api/compute/v1/attempts/{attempt_id}/fail
POST /api/compute/v1/attempts/{attempt_id}/cancel-ack
POST /api/compute/v1/attempts/{attempt_id}/artifact-urls
```

默认长轮询 25 秒、heartbeat 30 秒、lease 120 秒、重新派发宽限 10 分钟、最多三个 attempt。任务信封只包含注册 workflow、规范化输入、数据哈希、镜像 digest、Git SHA、资源限制、deadline、lease 和服务端签名；绝不包含代码、密码、API key、数据库/Redis 凭证或 Evidence 私钥。

没有 Worker 是 `QUEUED + waiting_for_worker`，不是科学能力缺口。旧 lease 的迟到结果不能成为证据。相同哈希的重复提交幂等成功，不同哈希返回 409 并记录安全事件。

## 6. Workspace、来源与审核

新增权威记录：

```text
research_workspaces
source_documents
source_extractions
claim_audit_reviews
worker_nodes
worker_enrollment_tokens
science_execution_attempts
```

Workspace 第一版只私有。来源和 extraction 都不可原地改写；已经进入 Audit 的来源必须创建新版本。每个原文 anchor 使用：

```text
sha256(source_document_hash + locator + raw_text)
```

审核记录只追加。只有 JWT 用户名位于 `SCIENTIFIC_REVIEWER_USERNAMES` 中才可审核；最终支持结论的审核者必须不同于 Audit 所有者。

旧研究记录迁移到每位用户的 `Imported research` Workspace。

## 7. Union3 Reader v1

唯一注册来源：

```text
source_profile_key=union3_arxiv_v1
canonical_identifier=2311.12098v4
```

其他论文或版本返回 `422 source_profile_not_registered`。读取顺序为 arXiv Atom 元数据、精确 v4 source tarball、精确 v4 PDF；HTML 只用于辅助展示。

权威定位：Section 5.3、PDF printed page 58、Table 9、Flat ΛCDM、SNe row、Ωm column。这个数值是 frequentist profile-χ² 的 68.3% 置信区间，不是 posterior。

第一版不使用 OCR。缺少可验证结构化来源时保持 `CAPABILITY_GAP`，不允许 AI 猜表格。

## 8. 首个科学合同

```text
数据：Union3 22-node distance product
模型：flat ΛCDM
自由参数：Ωm
固定参数：w=-1, Ωk=0
方法：profile-χ²
区间：Δχ²=1, 68.3%
```

固定文件：

```text
lcparam_full.txt  a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7
mag_covmat.txt    64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146
```

运行前检查 22 维测量向量、22×22 对称正定完整协方差、字段、单位、redshift 定义和 checksum。常数星等偏移解析边缘化，因此这个工作流不能测量 H0。

论文目标值用 Decimal 字符串：

```json
{
  "parameter": "omegam",
  "central": "0.356",
  "minus": "0.026",
  "plus": "0.028",
  "lower": "0.330",
  "upper": "0.384",
  "confidence_level": "0.683",
  "interval_kind": "frequentist_profile_chi_square",
  "model_scope": "flat_lcdm",
  "data_scope": "union3_sn_only"
}
```

主计算扫描 `Ωm∈[0.05,0.80]`，步长 0.0005；用 Brent 方法细化极小值和两侧 `χ²min+1` 根，并用一半步长复查稳定性。独立复算模块不得导入生产 Union3 likelihood，必须从原始文件独立构造距离和协方差投影。

机器验收：

- 41 个固定 Ωm 点的归一化 χ² 差异 ≤ 1e-4；
- 最佳值和两个端点的实现间差异分别 ≤ 2e-4；
- 网格加密后的最佳值和端点变化 ≤ 2e-4；
- 与论文中心相差 ≤ 0.1σ，两个区间宽度差异分别 ≤ 5%；
- χ²min 与论文四舍五入值 24.0 相差 ≤ 0.2，DoF = 20；
- 区间不触碰搜索边界。

MCMC 诊断 R-hat、ESS、MCSE 在本案例统一为 `not_applicable`。

## 9. Verdict 与两条车道

探索车道允许 AI 搜索、摘要、提出假设和写草稿，但永远是 DRAFT。证据车道必须按固定来源 → anchor → 数据检查 → 主计算 → 独立复算 → 人工审核 → deterministic Finalizer 的顺序执行。

等待审核时为：

```text
run_status=COMPLETED
scientific_verdict=WITHHELD
review_status=PENDING
```

所有机器门和独立审核通过后才可写：

```text
scientific_verdict=SUPPORTED
claim_scope=reproduction_of_published_constraint
reproduction_ready=true
publication_ready=false
```

允许的公开表述仅限于“使用固定版本 Union3 22-node 数据、完整协方差和 SNe-only 平直 ΛCDM profile-χ² 工作流，在预设容差内复现论文报告的 Ωm 置信区间”。不得称为 posterior、完整 2087 颗超新星重分析、H0 测量、暗能量演化发现、ΛCDM 证明/否定、同行评议或可直接发表的新发现。

## 10. Evidence Pack v2

内部运行 HMAC 保持兼容；最终 Pack 采用 Ed25519，并包括：

```text
manifest.json
manifest.sig
report.md
citations.bib
provenance.json
source_snapshot.json
anchors.json
claims.json
primary_analysis.json
independent_analysis.json
diagnostics.json
reviews.json
limitations.json
```

Manifest 使用 RFC 8785/JCS canonical JSON，列出每个文件的 SHA-256。包内不默认携带完整论文。Worker 控制签名 key 与 Evidence key 分离；包内公钥不能自证可信，离线工具必须对照官方 keyring。

```text
GET  /.well-known/standard-astro-evidence-keys.json
POST /api/public/evidence-packs/verify
astro evidence verify pack.zip
```

旧 HMAC Pack 不改写，继续走旧验证器。

## 11. UI 与公共 API

新增中英双语路由：

```text
/research
/research/workspaces/{workspace_id}
```

Workspace 固定标签为 Overview、Sources、Claims、Runs、Evidence Packs。普通用户只选择来源、候选主张和已注册 workflow，不输入 job id、dataset key、工具参数或 anchor id。

主要 API 包括 Workspace CRUD、来源创建/读取/重试、Workspace 下的 Claim Audit、revision、review queue 和 review append。所有读取、删除、下载和节点操作都必须进行 owner isolation。

## 12. 功能开关与合并顺序

默认关闭：

```text
CLAIM_AUDIT_ENABLED=false
RESEARCH_WORKSPACE_ENABLED=false
ARXIV_READER_ENABLED=false
UNION3_REPRODUCTION_ENABLED=false
EVIDENCE_PACK_V2_ENABLED=false
```

合并顺序：

1. 启动阻断：APP_ROLE、Alembic、CI、Render、Compose 和 boot matrix；
2. HTTPS Worker Gateway；
3. Workspace 与 Union3 Reader；
4. Union3 profile-χ² 主计算与独立验证；
5. 审核、Finalizer 与 Evidence Pack v2；
6. 双语 UI、文档和真实 Demo。

每一步先通过 focused tests，再进入全量 backend/frontend/科学审计；前一步未通过时，后一步代码保持 dark。

## 13. 备份、部署和发布门

- PostgreSQL 每日加密 `pg_dump` 到版本化对象存储，保留 30 天；
- 每月恢复到隔离 PostgreSQL 16；
- Evidence、删除 tombstone 和加密 key 分开备份；
- S3/R2 成为权威产物存储，Render disk 暂时只做打包和可重建缓存；
- `/health/ready` 检查 Backend、PostgreSQL 和 Alembic head；
- `/health/deep` 检查 Redis、control worker、Beat、S3、Evidence key、Worker registry 和 verification queue；
- 没有用户 Worker 只显示 `science_capacity=degraded`；没有独立验证器必须阻止 `SUPPORTED`。

发布前必须有：一个真实本地 Worker 登记、真实 Union3 v4 读取、真实主计算和独立复算、不同用户人工审核、可离线验签 Pack、数据库恢复演练、72 小时观察、Daily 连续 14 天、零 unsupported scientific escape、邀请制注册和关闭平台共享模型密钥。

72 小时观察和 14 天 Daily 是现实时间门，不能用 AI 压缩；代码完成与产品验证必须分别报告。

## 14. 后续扩展合同

每个新能力必须实现统一 `RegisteredWorkflowProfile`：固定 source profile、dataset pins、claim schemas、主执行器、独立验证器、允许/禁止主张、风险等级、阈值和 Pack 要求。

扩展顺序：DESI DR2 官方链后处理 → 上下限和一致性主张 → 相关性安全 Tension Lab → 注册 full-likelihood → Rubin/Euclid/Roman 正式适配 → 最后才评估新发现级工作流。

用户扩大从 3–5 位设计伙伴开始，稳定后到 10–15 人；28 天目标为至少 10 名真实用户、20 个 workflow、5 人返回。AI、机器人和 GitHub clone 不计为真实用户。

## 15. 实施检查表

- [ ] PR 0：角色化启动、CI、Render/Compose boot matrix 全绿；
- [ ] Worker：enrollment、Ed25519、nonce、lease、撤销、幂等、迟到结果；
- [ ] Workspace：模型、迁移、owner isolation、旧记录回填；
- [ ] Reader：Union3 v4 pin、Table 9 anchor、大小/解压/域名限制；
- [ ] Science：主实现、独立实现、41 点 parity、论文阈值；
- [ ] Review：不同用户审核、append-only、机器门不可覆盖；
- [ ] Evidence v2：JCS、Ed25519、keyring、篡改/轮换/旧包测试；
- [ ] UI：中英切换、刷新恢复、键盘、移动端；
- [ ] E2E：真实后端 + 真实本地 Worker + 下载并离线验签；
- [ ] 运维：恢复演练、dark deploy、72 小时观察、14 天 Daily；
- [ ] 发布：`v0.6.0-alpha.1` 与可公开验证的示例 Pack/Demo。
