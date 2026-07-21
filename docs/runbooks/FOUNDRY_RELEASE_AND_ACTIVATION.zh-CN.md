# Workflow Foundry v1 发布与激活手册

这份手册说明怎样把 Workflow Foundry 从“代码已经合并”安全地推进到“候选 Demo 可运行”，再推进到“人工批准后可正式注册”。任何一步失败，都停在当前阶段；不要跳过签名、审核或观察门。

## 1. 三个不同状态

```text
工程完成
→ dark deployment（功能仍关闭）
→ 候选能力开放（只能产生 NON_FORMAL_DEMO）
→ 正式注册开放（必须人工审核和签名）
```

- `engineering complete`：代码和自动测试通过，不代表生产环境已验证。
- `candidate enabled`：用户可以提交结构化能力缺口，AI 可以生成候选并运行 Demo；Demo 不能支持科研结论。
- `formal registration enabled`：准确候选版本经过人工审核、正式 Worker 构建和 Registry 离线签名后，才可进入正式目录。

72 小时生产观察和人工科学审核不能由自动测试代替。

## 2. GitHub 保护环境

先建立下列独立 GitHub Environments，并为正式环境配置 required reviewers：

| Environment | 用途 | 可访问的秘密 |
|---|---|---|
| `foundry-candidate-draft-ai` | AI 生成不可执行草稿 | `FOUNDRY_AI_DRAFT_API_KEY`；provider command 作为 Environment variable |
| `foundry-candidate-draft-build` | 物化补丁并推送非正式镜像 | 无长期秘密；保护 `packages:write` 的临时 `GITHUB_TOKEN` |
| `foundry-candidate-draft-callback` | 只上传冻结的 Draft 回执 | `FOUNDRY_DRAFT_RESULT_SECRET`；控制面 URL 作为 Environment variable |
| `foundry-candidate-validation` | 隔离 Demo 与结果回调 | `FOUNDRY_VALIDATION_CALLBACK_URL`、`FOUNDRY_VALIDATION_RESULT_SECRET` |
| `foundry-materialization-pr` | 创建确定性分支与 Draft PR | 无长期秘密；保护 contents/PR write 临时令牌 |
| `foundry-materialization-build` | 从已合并源码构建非正式镜像 | 无长期秘密；保护 packages write 临时令牌 |
| `foundry-materialization-attestation` | 签名 PR/merge 身份回执 | 独立 materialization Ed25519 私钥 |
| `foundry-materialization-callback` | 上传已经签名的物化回执 | 独立 materialization callback bearer；无签名私钥 |
| `foundry-formal-worker` | 构建、验证和签名正式多架构镜像 | Cosign/OIDC、独立构建证明私钥与回调身份 |
| `foundry-formal-worker-failure` | 自动记录正式构建失败，只能关闭一次 attempt | `FOUNDRY_FORMAL_BUILD_FAILURE_RESULT_SECRET`；无签名私钥、OIDC、checkout 或成功回调身份 |
| `foundry-registry-release` | 人工批准后签名 RegistrySnapshot | 独立 Ed25519 Registry 私钥 |
| `foundry-registry-activation-pr` | 导出已验签 import，生成只含公开材料的 PR | 独立 activation bearer；无签名私钥 |
| `foundry-registry-activation` | 按精确 commit 部署并写入回执 | Render API key、独立 activation bearer；无签名私钥 |

**硬性发布门：**上表每一个 Environment 的 `Deployment branches and tags`
必须设置为 `Selected branches and tags`，并且只允许 `main`。不能选择“所有分支”，
也不能只依赖 workflow 中的 `GITHUB_REF` shell 检查；分支上的 workflow 可以删除
shell 检查，只有 GitHub Environment 的部署分支策略能在 Job 启动和发放秘密/令牌前
阻止它。没有配置这项策略时，`FOUNDRY_AI_DRAFTING_ENABLED`、
`FOUNDRY_AUTO_DEMO_ENABLED` 和正式注册开关必须保持 `false`。

四个候选 Environment（Draft AI、Draft Build、Draft Callback、Validation）为了让
候选 Demo 自动运行，**不要求** GitHub `Required reviewers`；它们只要求强制的
`main` 部署分支策略。Materialization PR、Build 和 Callback 也不要求 reviewer，
因为它们只创建 Draft PR、构建非正式镜像或上传已签名回执；
`foundry-materialization-attestation` 必须至少有 1 名人工 reviewer。
人工批准仍发生在 Candidate 的工程/科学审核与正式注册阶段。
`foundry-formal-worker`、Registry Release、Registry Activation PR 与 Registry
Activation 也必须设置人工 `Required reviewers`。唯一例外是
`foundry-formal-worker-failure`：它必须保持无 reviewer，才能在构建失败后自动关闭
attempt；但仍必须只允许 `main`。这个 Environment 只能获得 failure-only bearer，
不能获得正式构建回调 bearer、attestation 私钥、OIDC 或仓库读取权限。不能主动给它
增加 reviewer，否则失败记录会再次卡住。

Registry 私钥不得出现在 Render、候选 Runner、本地 Worker 或浏览器。AI 身份不得成为 GitHub Environment reviewer，也不得获得 review、register、suspend 或 revoke API 权限。

所有 GitHub Actions dispatcher 必须固定使用 `ref=main`，workflow 也必须检查
`GITHUB_REF=refs/heads/main`；禁止 `pull_request_target`，第三方 Action 必须固定到完整 commit SHA。

## 3. Render 控制面配置

先部署数据库迁移和代码，所有开关保持关闭：

```text
WORKFLOW_REGISTRY_V2_ENABLED=false
FOUNDRY_GAP_TRACKING_ENABLED=false
FOUNDRY_CANDIDATE_CATALOG_ENABLED=false
FOUNDRY_AI_DRAFTING_ENABLED=false
FOUNDRY_AUTO_DEMO_ENABLED=false
FOUNDRY_SOURCE_MATERIALIZATION_ENABLED=false
FOUNDRY_REGISTRATION_ENABLED=false
```

为 Draft、Validation、Formal Build 成功回调、Formal Build 失败回调和 Registry
Release 分别使用不同的 GitHub token 与回调秘密。任何两个秘密都不得相同。GitHub
token 只授予目标仓库所需的最小 workflow dispatch 权限。

Render 必须同时配置下面两个互不相同、至少 32 字符的 bearer：

```text
FOUNDRY_FORMAL_BUILD_RESULT_SECRET=<成功 attestation 回调专用>
FOUNDRY_FORMAL_BUILD_FAILURE_RESULT_SECRET=<失败 attempt 关闭专用>
```

前者只能存在于需要人工审批的 `foundry-formal-worker` Environment；后者只能存在于
自动的 `foundry-formal-worker-failure` Environment。控制面使用两个不同的认证器：
failure bearer 调用成功 attestation endpoint 会得到 403，成功 bearer 调用 failure
endpoint 也会得到 403。

两个 Environment 都要设置同一个非秘密变量
`FOUNDRY_CONTROL_PLANE_URL=https://<backend-host>`；failure Environment 不应再添加
任何其他 secret。创建后检查其 Deployment branch policy 是只允许 `main`，并确认
`Required reviewers` 为空。

控制面只信任配置好的构建证明公钥和 Registry 公钥 keyring。Registry 导入仅保存“已验签、等待部署”的不可修改收据，不会热更新正在服务的进程。

Render 的 API 指定 `commitId` 并不会自动关闭 auto-deploy。因此，在生成第一份 activation PR 以前，必须先把以下三个 Blueprint 服务的 `autoDeployTrigger` 部署为 `off`：

```text
standard-astro-backend
standard-astro-celery-worker
standard-astro-celery-beat
```

否则 activation PR 合并后，Render 可能在人工激活门之前自动启动新快照。普通代码发布也因此改为受控的精确 commit 发布，不再依赖 push 自动上线。

另外配置独立的：

```text
FOUNDRY_REGISTRY_ACTIVATION_RESULT_SECRET=<至少 32 字符且不复用>
```

它只能读取已经验签的公开 import、查询新进程状态和追加部署回执，不能签名 Registry，也不能直接修改候选或正式目录。

正式构建使用独立于 Registry 的 Ed25519 信任域：

```text
GitHub Environment secret:
FOUNDRY_FORMAL_BUILD_ATTESTATION_PRIVATE_KEY=<base64 32-byte seed>

GitHub Environment variable:
FOUNDRY_FORMAL_BUILD_ATTESTATION_KEY_ID=<stable key id>

Render public configuration:
FOUNDRY_FORMAL_BUILD_ATTESTATION_VERIFICATION_KEYS={"<key id>":"<base64 public key>"}
```

私钥不能放到 Render；Registry 私钥也不能拿来签构建证明。回调 bearer 只负责访问控制，控制面还会离线验证 Ed25519 签名、GitHub repository/workflow、源码 commit/tree 和镜像 digest。任何布尔型 `sigstore_verified` 声明都不构成信任。

## 4. 候选序列启用

按顺序开启，每一步完成一次重启和回归检查：

1. `WORKFLOW_REGISTRY_V2_ENABLED=true`，只读取当前经过验证的正式快照。
2. `FOUNDRY_GAP_TRACKING_ENABLED=true`，确认结构化 `CAPABILITY_GAP` 可以创建去重请求。
3. `FOUNDRY_CANDIDATE_CATALOG_ENABLED=true`，确认普通用户只能看到自己请求关联的候选。
4. `FOUNDRY_AI_DRAFTING_ENABLED=true`，确认 AI Job 只输出受限补丁、WorkflowSpec 和 SBOM，不执行候选代码。
5. `FOUNDRY_AUTO_DEMO_ENABLED=true`，确认 Validation Runner 无 AI 密钥、无生产凭据、默认无网络，并把结果写成不可修改 Demo 记录。
6. `FOUNDRY_SOURCE_MATERIALIZATION_ENABLED=true`，仅在受保护 GitHub Environment、公钥 keyring、回调密钥和人工 PR 审核都已配置后开启。详细步骤见 [AI 候选代码落盘与合并手册](./FOUNDRY_SOURCE_MATERIALIZATION.zh-CN.md)。

首个候选固定为 DESI DR2 官方 chain 完整性与参数区间摘要。验收记录必须包含 candidate/version/run ID、输入与源代码哈希、runner digest、stdout/stderr 哈希、资源使用和失败分类。

无论 Demo 是 `PASSED`、`PARTIAL` 还是 `FAILED`，它都必须保持：

```text
evidence_class=NON_FORMAL_DEMO
publication_ready=false
claim_eligible=false
```

候选页面必须持续显示“候选 / Candidate”“非正式 / Non-formal”“不能支持科研结论”。

## 5. 正式序列启用

只有候选序列稳定后才设置 `FOUNDRY_REGISTRATION_ENABLED=true`。一次正式注册必须完整经过：

1. 工程审核者批准准确的 `candidate_version_hash`。
2. 科学审核者独立批准同一个哈希。
3. 受保护的 Formal Worker workflow 从 `main` 重建源码、测试 AMD64/ARM64、生成 SBOM/provenance，并签名准确镜像 digest。
4. 控制面独立验证构建证明，而不是相信 callback 的布尔字段。
5. Registry Release 环境由人工批准，离线私钥签名完整 RegistrySnapshot。
6. 控制面验签并保存 `SIGNED_READY_FOR_DEPLOYMENT` 收据；此时仍未激活。
7. 人工运行 `foundry-registry-activation-pr.yml`。它从控制面重新导出准确的已验签 import，并在本地再次验签；随后只把 `active-signed-registry.json`、公开 keyring 和 hash manifest 写入受保护 PR。私钥永远不进入仓库。
8. 人工审核并合并 activation PR。此时仍未记录为 ACTIVE；仓库中的公开材料只会在新进程启动时被读取，不能热切换正在运行的 Registry。
9. 在当前 `origin/main` 的精确 40 位 commit 上运行 `foundry-registry-activation.yml`。创建任何 Render deploy 以前，控制面 preflight 必须确认该 import 仍是唯一 chain head，并且严格晚于持久化的 ACTIVE receipt；已激活版本重放和“从未激活但已经过期”的旧 import 都会被拒绝。通过后，workflow 分别调用 `POST /v1/services/{serviceId}/deploys`，用同一个 `commitId` 部署 Backend、Control Worker 和 Beat；随后只通过 `GET /v1/services/{serviceId}/deploys/{deployId}` 轮询这三个准确 deploy，并核对返回的 `commit.id`。
10. 新进程启动时完成签名、文件哈希、base hash、epoch、回滚保护、import 收据和数据库 projection 一致性检查；任何一项失败都不能接流量。Control Worker 必须和 Backend 使用同一 commit，且 verification queue 可用。
11. 只有新进程返回 `ACTIVATION_READY` 后，workflow 才能向控制面追加不可修改的 activation receipt。准确的注册候选此时已经由启动 reconciler 投影为 `PROMOTED`；`import` 本身永远不等于 `active`。
12. 本地 Worker 只广告镜像内静态 entrypoint 和 ToolSpec hash；服务端只租赁与其实际 image digest/能力相容的任务。

新的 AI 科学代码即使 Demo 成功，如果还没有进入正式 Worker 的静态 ToolSpec 表，也必须返回 `registry_release_entrypoint_not_static`，不能注册。

### 5.1 正式签名前的供应链门

Formal Worker workflow 在 Cosign 和 Ed25519 签名前运行三类离线检查，任何一项失败都不会产生正式构建证明：

1. **依赖完整性**：`requirements.lock` 中每个依赖必须是准确版本并带 SHA-256；正式镜像继续用 `pip install --require-hashes` 安装；AMD64 和 ARM64 镜像分别执行 `python -m pip check`，并把实际安装版本与当前平台生效的 lock pin 逐项比较。候选不能增加 URL、editable 或未进入 lock 的依赖。
2. **许可证清单与最小政策**：从两个架构镜像的 Python 已安装元数据和许可证文件生成清单。缺少任何许可证证据会失败；AGPL、SSPL、Business Source License、Elastic License、Commons Clause、专有许可证和非商业限制会失败。该自动政策只是发布阻断条件，**不等于法律意见或完整许可证兼容性审查**。
3. **秘密扫描**：扫描准确 Git source manifest 中的全部文件，并单独列出 `backend/app/services/foundry_generated/` 候选路径。检查私钥块和常见线上 token 形状；报告只保存路径、文件哈希、规则 ID 和行号，不保存疑似秘密正文。例外仅允许测试目录中的准确 `path + SHA-256 + pattern_id`，文件变化后例外自动失效。

受保护的政策文件是：

```text
backend/foundry_policy/formal-release-policy-v1.json
```

正式构建材料新增以下规范化 JSON 收据：

```text
release-audit/static/dependency-lock-receipt.json
release-audit/static/secret-scan-receipt.json
release-audit/static/static-audit-receipt.json
release-audit/linux-amd64/dependency-integrity-receipt.json
release-audit/linux-amd64/license-policy-receipt.json
release-audit/linux-amd64/environment-audit-receipt.json
release-audit/linux-arm64/dependency-integrity-receipt.json
release-audit/linux-arm64/license-policy-receipt.json
release-audit/linux-arm64/environment-audit-receipt.json
release-audit/formal-release-audit-receipt.json
```

汇总收据固定记录：`status=PASSED`、政策哈希、source tree/lock/SBOM 哈希、两个架构和九份子收据哈希。正式构建签名 payload、数据库 `FoundryFormalBuildAttestation` 以及 Registry release context 都继续绑定汇总哈希和子收据哈希；只改变任意一字节都会阻止注册或 Registry 导入。

这里没有联网查询漏洞公告数据库，因此收据必须保持：

```text
advisory_database_checked=false
vulnerability_status=NOT_EVALUATED
legal_review_complete=false
```

这套门禁只能证明锁文件和已安装环境一致、许可证元数据符合当前最小政策、源码没有命中已登记的秘密形状；**不能写成“没有 CVE”或“已经完成法律审查”**。

## 6. 回滚与撤销

- 候选失败：保留旧版本、Demo 和事件，创建新 candidate version；不得覆盖历史。
- 正式构建失败：保留失败回执，修复后重新构建同一准确版本或创建新版本。
- Registry 部署失败且新进程尚未接流量：保留仍在服务的旧进程，修复后重新部署同一个准确 commit。已经导入更高版本后，不允许把旧 snapshot 当作新激活；需要签署一个向前推进的替代/撤销 release，避免绕过防回滚门。
- `SUSPENDED`/`REVOKED`：立即停止发放新 lease，并在接收结果和 Finalizer 前再次检查状态。
- 已发出的旧结果可以保留审计记录，但不得越过撤销状态变成 `SUPPORTED`。

## 7. 观察记录

dark deployment 后连续观察 72 小时，至少记录：

- `/health/ready` 与 `/health/deep`；
- PostgreSQL migration head、Redis、S3 和控制队列；
- Draft、Validation、Formal Build 和 Registry dispatch 的失败率；
- Demo 重启恢复、重复 callback 幂等和旧版本 callback；
- Worker enrollment、lease、heartbeat、迟到结果与撤销；
- `unsupported scientific escape`、`unsigned registration`、`AI self-approval`、`candidate-to-formal bypass`、`sandbox escape`，目标都为 0。

观察通过后才能把状态写成 `formal registration open`。若没有完成真实 72 小时观察，应明确记录：

> engineering complete; production observation pending
