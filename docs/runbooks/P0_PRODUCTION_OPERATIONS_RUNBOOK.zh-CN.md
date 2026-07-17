# Standard Astro P0 生产迁移与观察 Runbook

- 状态：执行前版本；尚未证明真实生产部署完成
- 适用范围：P0 基线、备份恢复、平行 Render 栈、数据库迁移、30 分钟切换、回滚、72 小时观察和 Daily 14 天台账
- 记录模板：[P0 生产执行记录模板](./P0_PRODUCTION_RECORD_TEMPLATE.zh-CN.md)

术语说明见[路线图 §3 本科生版关键术语](../roadmaps/P0_P1_COMPLETE_PLAN.zh-CN.md#3-本科生版关键术语)。

> 这份 Runbook 告诉操作员“按什么顺序做”和“什么情况下必须停”。它不会创建付费资源、读取生产密钥或授权流量切换。所有付费 Render 资源、生产密钥、维护窗口、流量切换和外部邀请，都必须由用户明确批准。

## 1. 权威来源与避免重复

本 Runbook 是中文执行导航，不替代已有安全合同：

- [英文 Operations Runbook](../OPERATIONS_RUNBOOK.md)：备份、恢复、健康检查、key rotation 和 incident 规则。
- [Production Cutover Checklist](../PRODUCTION_CUTOVER_CHECKLIST.md)：切换授权边界。
- [`render.yaml`](../../render.yaml)：目标 Render 拓扑。
- [`backup.sh`](../../backend/scripts/ops/backup.sh)：带 manifest 的可移植备份。
- [`restore.sh`](../../backend/scripts/ops/restore.sh)：只允许恢复到空数据库和新存储路径的 fail-closed 恢复。
- [`run_legacy_uuid_migration_drill.sh`](../../backend/scripts/ops/run_legacy_uuid_migration_drill.sh)：真实 PostgreSQL 16 旧 schema 迁移演练。
- [`verify_deployment.py`](../../backend/scripts/verify_deployment.py)：验证 readiness、deep health 和完整 release identity。
- [`daily_blind.sh`](../../backend/scripts/daily_blind.sh)：Daily 真实聊天路径。

如果本 Runbook 与这些文件冲突，先停止执行，选择更严格的要求，并在代码评审中统一文档。

## 2. 角色和授权

| 角色 | 责任 | 不得自行决定 |
|---|---|---|
| 用户/发布负责人 | 批准预算、密钥、维护窗口、切流量和删除旧资源 | 不适用 |
| 主操作员 | 执行 runbook、记录时间、判断 stop/go、发起回滚 | 不能跳过门槛以赶进度 |
| 数据库复核人 | 复核备份、restore、schema diff、migration/stamp | 不能在旧生产库上试验 |
| 科学复核人 | 检查 DESI 清单、Evidence Pack 和 Daily 科学门 | 不能把探索结果升级为可发表证据 |
| 观察员 | 记录 72h 和 14d 台账 | 不能把缺失数据补写成通过 |

最低双人复核项：生产 stamp、最终写冻结、切流量、回滚、删除旧资源和签名 key rotation。

## 3. 全局 Stop 条件

任一条件成立，立即停止当前阶段：

- 不知道当前完整 Git SHA、Alembic heads 或目标数据库；
- 备份只存在于被保护的同一块磁盘；
- restore 没有在隔离空数据库成功；
- Fernet key ID、evidence signing key ID 或 retained verification keyring 不清楚；
- 旧数据库 schema 与预期不同，但有人建议直接 `alembic stamp`；
- Backend、Worker、Beat 不是同一完整 Git SHA；
- `/health/ready` 或 `/health/deep` 非 200；
- 匿名请求能使用平台付费模型密钥；
- 对象 checksum、证据签名或历史 BYOK 恢复失败；
- 正常切换写冻结已达到 30 分钟且没有完成安全切换或触发 incident rollback；
- 出现 unsupported scientific escape；
- 任何人要求把 72h、14d 或 28d 的未来时间提前标为完成。

## 4. Phase 0：建立基线和 SHA 清单

### 4.1 代码基线

在执行时最新的 `origin/main` 上建立发布分支，记录：

```bash
git fetch origin
git status --short
git rev-parse HEAD
git rev-parse origin/main
git log -1 --format='%H %cI %s'
cd backend
./venv/bin/alembic heads
```

要求：

- 工作树是否干净要如实记录；不能静默丢弃用户改动。
- 发布 commit 必须是 40 位 SHA，不能只记录 branch 名或 `latest`。
- 锁文件和部署声明也进入清单：`backend/requirements.lock`、frontend lockfile、`render.yaml`、Alembic revisions。

### 4.2 严格 DESI 科学清单

由科学工作流在其独占原路径生成清单。至少列出：

- canonical config；
- frozen protocol 和 amendment；
- chain 文件；
- checkpoint/收敛文件；
- analysis/grade 代码；
- answer-key 隔离文件；
- Python 和依赖环境收据；
- 每个文件的大小和 SHA-256。

macOS 可用 `shasum -a 256 <file>`，Linux 可用 `sha256sum <file>`。不要手工复制哈希；把命令输出保存到只读记录中，再由第二人抽查至少一个大文件和一个配置文件。

### 4.3 独立副本

- 代码可由 Git 恢复，但未入 Git 的 chains、收据和数据必须复制到不同故障域。
- “同一台机器的另一个目录”不是独立备份。
- 记录源路径、目标、复制开始/结束时间、对象数量、总字节和清单哈希。
- 恢复抽样成功之前，不进入 Phase 1。

把所有字段填入[记录模板 §2](./P0_PRODUCTION_RECORD_TEMPLATE.zh-CN.md#2-安全基线记录)。

## 5. Phase 1：旧生产环境盘点

只读盘点下列内容：

1. Render 服务名、类型、plan、region、URL、自定义域名和当前 commit。
2. PostgreSQL 版本、数据库名、容量、PITR 窗口、连接目标和 `alembic_version` 是否存在。
3. Redis plan、持久化和 eviction policy。
4. Backend、Worker、Beat、Frontend 的环境变量名称；只记录 key ID，不记录 secret 值。
5. 持久磁盘、对象存储 bucket、versioning、lifecycle 和 replication。
6. 正在运行和排队的科研任务、Beat 计划、登录会话、历史记录、BYOK 行和证据签名版本。
7. 哪个入口负责停止写入，哪个入口负责恢复写入。

禁止把数据库 URL、API key、JWT、Fernet 或 signing secret 写入模板、日志或 Git。

## 6. Phase 2：完整备份与隔离恢复

### 6.1 创建一致性备份

首选同时具备：

- Render PostgreSQL PITR recovery point；
- `backup.sh` 生成的 portable bundle；
- S3/R2 provider-native versioning/replication 或一致性导出；
- 持久磁盘 snapshot/export；
- 外部 secret manager 中匹配的 key IDs 和 retired verifier keyring。

按照[英文 Runbook 的 Portable backup](../OPERATIONS_RUNBOOK.md#portable-backup)执行现有脚本。`backup.sh` 不会导出 S3 bucket，因此对象存储必须单独保护。

记录：backup ID、commit、Alembic revision、bundle SHA-256、对象存储版本点、key IDs、开始/结束时间和存储位置。目标 RPO/RTO 只是目标，不是成功声明。

### 6.2 隔离恢复

恢复目标必须同时满足：

- 全新 PostgreSQL 数据库；
- 没有非系统 schema/object；
- 全新且不存在的 storage path；
- 与 manifest 完全相同的代码 commit；
- 匹配的 Fernet 和 evidence key IDs；
- 与生产网络隔离，不能被真实用户访问。

使用现有 [`restore.sh`](../../backend/scripts/ops/restore.sh)，不要自己写临时 `pg_restore` 绕过检查。

恢复后验证：

- `alembic current` 与 manifest 一致；
- `alembic check` 无待执行操作；
- `/health/ready` 和 `/health/deep`；
- 一个登录账号；
- 一个历史会话；
- 一个加密 BYOK 读取；
- 一个对象按 checksum 下载；
- 一条历史证据用 retired key 验证；
- 一条新证据用 current key 签名和验证。

恢复失败后丢弃整个恢复数据库和目标路径，重新创建；不要在半恢复环境上继续补修。

## 7. Phase 3：安全处理无版本旧数据库

### 7.1 先判断，不先 stamp

在生产 clone 上检查：

```sql
SELECT to_regclass('public.alembic_version');
```

分支 A：存在 `alembic_version`

- 记录当前 revision；
- 在 clone 上执行正常 upgrade rehearsal；
- 运行 migration tests 和代表性数据校验。

分支 B：不存在 `alembic_version`

1. 只在 clone 上导出完整 schema：表、列、类型、默认值、nullable、PK、FK、unique、index、enum、extension 和约束。
2. 用当前迁移链在另一个空 PostgreSQL 16 数据库重建期望 schema。
3. 对两份完整 schema 做结构化比较，不只比较表名。
4. 完全一致时，才允许在 clone 上 `alembic stamp <matching-revision>`。
5. stamp 后执行 `alembic check`、upgrade rehearsal 和代表性行检查。
6. 有任何差异就停止，写 reviewed bridge migration；不能 stamp 掩盖差异。

生产 stamp 的前提：clone 证明、最新备份、双人复核、明确 revision、可回滚资源和用户批准全部具备。即便满足，也只执行一次并留下完整记录。

### 7.2 Bridge migration 要求

- 能识别预期旧状态；遇到未知状态 fail closed。
- 在一个事务内完成可事务化的结构变化。
- 对脏数据先检测，不能静默截断或强转。
- 有真实 PostgreSQL 16 fixture 和失败回滚测试。
- 迁移前后记录行数、关键约束和代表性数据哈希。

复用 [`run_legacy_uuid_migration_drill.sh`](../../backend/scripts/ops/run_legacy_uuid_migration_drill.sh) 的现有演练方式，不在生产库上开发迁移。

## 8. Phase 4：建立平行 Render 栈

### 8.1 需要用户批准的资源

- PostgreSQL 16；
- Redis/Key Value，持久化且 `noeviction`；
- Backend；
- Celery Worker；
- Celery Beat；
- Frontend；
- 持久磁盘；
- 版本化 S3/R2 bucket；
- 必要的日志、监控和域名变更。

[`render.yaml`](../../render.yaml) 不会替用户决定预算、region、capacity，也不会自动创建外部 S3/R2。批准前只做审查，不同步 Blueprint。

### 8.2 初始功能开关

至少保持：

```text
SHARED_DEEPSEEK_API_KEY_ENABLED=false
CLAIM_AUDIT_ENABLED=false
```

当 P1 invite-only 实现存在后再设置：

```text
SIGNUP_MODE=invite_only
```

不要在变量尚未被代码支持时，仅靠设置字符串假装功能已经生效。

### 8.3 Secret 和存储

- 保留原 JWT/Fernet 值，除非另有经过迁移的 rotation 计划。
- Evidence signing key 与 JWT 分离，并保留旧 verification keys。
- Backend、Worker、Beat 必须使用同一数据库、Redis、对象存储和 keyring。
- 开启 bucket versioning，并记录 retention/lifecycle。
- 匿名请求不得使用平台付费密钥。

### 8.4 切流量前验证

运行：

```bash
cd backend
EXPECTED_COMMIT=<40-character-render-git-sha> \
  ./venv/bin/python scripts/verify_deployment.py https://<target-backend>
```

并人工验证：

- `/health/ready` = 200；
- `/health/deep` = 200；
- Backend、每个 Worker、每个 active Beat lease 都报告同一完整 SHA；
- BYOK 请求成功；
- 匿名/无 BYOK 请求无法走平台付费 key；
- 一个真实科研任务从 API 进入 Worker 并到 terminal state；
- 任务进度、数据库行和对象存储结果一致；
- 登录、历史会话、加密 key、历史和新证据均可恢复；
- Frontend 指向 target API，而不是旧环境；
- Daily 固定回归通过。

任何一项失败都不允许切流量。

### 8.5 切换前冻结观察阈值

在切换前填写并批准[记录模板 §7.4](./P0_PRODUCTION_RECORD_TEMPLATE.zh-CN.md#74-观察基线和阻断阈值)。阈值不能在看到事故后临时放宽。

硬阻断项固定为：

- 任一计划检查点 `/health/ready` 或 `/health/deep` 非 200；
- Backend、Worker、Beat 任一完整 SHA 不一致；
- object checksum failure、evidence verification failure 或 unsupported scientific escape 大于 0；
- 固定登录、BYOK、匿名付费路径阻断或 Worker smoke 失败。

DB/Redis p95 latency、5xx/error rate、queue depth、oldest queued age、stale task 和最大允许监控缺口，需要根据旧环境基线和目标容量预先填写绝对阈值。任一必填阈值为空就是 No-Go。

## 9. Phase 5：30 分钟切换窗口

本节的 30 分钟从“开始写冻结”计时。准备工作必须提前完成，不能把 T-30 到 T=0 的准备时间算入另一段隐藏的冻结。

### 9.1 T-24h 到 T-1min：不冻结写入的准备阶段

- 再确认批准人、操作员、回滚人和沟通渠道。
- 确认旧资源仍可快速接管流量。
- 完成最后一次 restore sample。
- 记录 in-flight/queued jobs 和处理策略。
- 降低 DNS TTL（如果适用且已提前批准）。
- 准备同一窗口的数据库和对象存储最终同步命令。
- 预热 target、运行 verifier，并确认观察阈值已经冻结。

### 9.2 T=0：开始写冻结

- 停止新登录写入、聊天保存、上传、任务创建和 Beat 新调度。
- 允许已批准的在途任务结束；到截止时间仍未结束的任务按记录策略明确取消。
- 记录最后成功写入时间和最后任务 ID。

### 9.3 T+0 到 T+10min：最终一致性点

- 创建最终数据库恢复点和 portable backup。
- 完成对象存储增量同步，并验证对象数量、总字节和抽样 checksum。
- 对比关键表行数和关键对象清单。
- 在 target 执行 migration，Backend 是唯一 schema writer。
- Worker/Beat 等待 exact schema head 后启动。

### 9.4 T+10 到 T+20min：验证并原子切换

- 再运行 deployment verifier 和登录/任务/证据 smoke。
- 验证最后权威写入 ID/时间已经包含在 target。

同时切换：

- public API；
- Frontend API URL；
- Worker/Beat 的数据库、Redis 和 storage；
- 数据库和对象存储主引用；
- 必要的域名或代理配置。

不能让前端在新 API、Worker 却仍写旧数据库。旧环境切为只读。

### 9.5 T+20 到 T+30min：首轮验收并结束冻结

每 2–5 分钟检查 readiness/deep、登录、BYOK、队列、Beat、checksum、证据和错误率。所有硬阻断项为 0 且指标低于预先批准阈值，才可结束写冻结。任何 stop 条件触发立即回滚。

如果 30 分钟内不能安全完成，不继续“边修边切”；立即进入 incident rollback 并另约窗口。30 分钟是正常切换完成或触发回滚的预算，不是事故恢复时允许丢数据的期限。一旦进入 incident rollback，写冻结持续到 reconciliation 完成和双人批准恢复写入，实际可能超过 30 分钟，必须单独记录。

计划数据丢失量为 0。实际 RPO、downtime 和未处理任务必须从记录计算，不能默认写 0。

## 10. Phase 6：回滚

### 10.1 自动触发回滚的情况

- readiness/deep 失败或不稳定；
- Backend/Worker/Beat commit 不一致；
- schema revision 不一致；
- 数据行数、对象 checksum 或权限错误；
- 登录、BYOK、历史会话或加密 key 失败；
- evidence verification 回归；
- Worker 任务丢失、重复或长期卡住；
- unsupported scientific escape；
- 维护窗口超时。

### 10.2 回滚顺序

如果回滚发生在正常切换写冻结结束前，target 不应有用户写入，仍要核对受控 smoke 写入。如果回滚发生在已经恢复用户写入之后，必须假定 target 含有唯一的新数据，并执行下面的完整 reconciliation。

1. 维持全局写冻结，记录决定时间和触发证据；在 reconciliation 完成前，旧栈和 target 都不得接受新用户写入。
2. 阻止 target 接受新任务和 Beat 调度，为 target DB 和 object storage 创建事故快照。
3. 记录最后一次切换前一致性点，以及 target 在切换后接受的最后权威写入 ID、时间和对象版本。已经被 target 确认的写入是必须保全的数据。
4. 选择保留的旧资源或预先验证的 PITR clone 作为回滚目标，但先保持只读。
5. 生成 target 与回滚目标之间的行级/对象级差异清单。由数据 owner 审核后，使用幂等、可重复的方式反向同步或重放 target 新写入。
6. 复核关键行数、唯一约束、最后写入 watermark、对象数量/checksum、登录、任务和证据；记录重复、冲突和人工决定。
7. 将 API、Frontend、Worker、Beat、DB 和 storage 一起指向已追平的回滚目标，仍保持写入关闭，完成只读 smoke。
8. 数据库复核人和发布负责人双人批准后，才恢复用户写入。如果无法证明零丢失，保持只读/维护模式并升级 incident，不能为了 RTO 丢数据。
9. 不对生产直接执行未经演练的 Alembic downgrade。

修复后重新走 backup/restore、target validation 和完整切换流程。72 小时时钟从新切换完成时重新开始。

## 11. Phase 7：连续 72 小时观察

### 11.1 观察点

至少记录：T+0、1h、2h、4h、8h、12h、24h、36h、48h、60h、72h。高风险阶段可更频繁。

每个观察点记录：

- `/health/ready` 和 `/health/deep`；
- DB p50/p95 latency、连接数和错误；
- Redis latency、memory、eviction 和 queue depth；
- Worker active/reserved/failed/stale；
- Beat lease、最后 tick 和 commit；
- object checksum failure；
- evidence verify failure；
- login/BYOK/anonymous-paid-path；
- HTTP 4xx/5xx 和应用错误率；
- Daily run ID 和 verdict；
- incident、owner 和处理结果。

### 11.2 通过规则

- 72 个连续小时中没有硬阻断项，也没有超过 §8.5 预先批准的量化阈值。
- 中间发生回滚或新 release，观察时钟重新计时。
- 每个检查点都要与切换前 baseline 和阈值比较，不能只写“看起来正常”。
- 监控缺失不能当作健康。缺口只有在允许时长内且能从权威监控补回完整数据时才可保留 streak；超出预先批准时长或无法补回时，72 小时时钟归零。
- 只有记录完整并经复核，才可停止旧计算资源。
- 旧 DB snapshot 和对象备份至少保留 30 天；删除付费资源仍需用户批准。

## 12. Phase 8：Daily 连续 14 天台账

这个台账用于决定是否打开 Claim Audit，不等同于 P0 的 Daily 三连。

计时最早从包含最终 Daily 证据门的候选生产版本部署后开始；不能使用修复前的历史绿灯补足天数。它可以和 72 小时观察及 P1 工程并行。

每天的有效通过需要：

- 完整 Daily 运行，而不是只跑容易的子集；
- 使用记录的 provider/model 和完整 commit；
- 没有 HARD-FAIL 或 ERROR；
- 结果 artifact 和 `verdicts.json` 可下载；
- B4/B5/C2/F2 没有 unsupported escape；
- 外部依赖错误被修复并在同一日得到有效完整重跑，才能算当天通过。

连续规则：

- 有效 PASS：streak +1；
- HARD-FAIL、ERROR、未运行或 artifact 缺失：当天不是 PASS，连续 streak 归零；
- evaluator false positive 必须有单独修复、复核和重跑，不能靠手工改表继续 streak；
- 变更证据门或主要模型/provider 时，发布负责人决定是否重新开始；默认重新开始更安全。

达到 14 天后仍要同时确认隐私、删除、Evidence Pack、readiness/deep 和固定回归通过，才能将 `CLAIM_AUDIT_ENABLED` 从 false 改为 true。

## 13. P0 关闭条件

只有下列项目全部有记录，才能关闭 P0：

- Daily B4/B5/C2/F2 各 5 连过，完整 Daily 3 连过；
- 严格 DESI v1 Evidence Pack 完整并保持 `WITHHELD`；
- 隔离恢复成功；
- target health、commit、BYOK、Worker、storage 和 evidence 全部通过；
- 生产切换实际完成；
- 72h 观察完整且无回归；
- 旧备份保留计划已落实；
- Apache-2.0、DCO、数据许可清单、部署隐私说明和 prerelease 已发布。

在这些条件满足前，状态只能写：

> P0 implementation in progress；production acceptance pending。
