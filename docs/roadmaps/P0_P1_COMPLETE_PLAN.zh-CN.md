# Standard Astro：P0 + P1 AI 并行压缩版完整计划

- 状态日期：2026-07-17
- 目标读者：本科生、设计伙伴、开发者和生产运维人员
- 配套运维手册：[P0 生产迁移 Runbook](../runbooks/P0_PRODUCTION_OPERATIONS_RUNBOOK.zh-CN.md)
- 配套记录表：[P0 生产执行记录模板](../runbooks/P0_PRODUCTION_RECORD_TEMPLATE.zh-CN.md)

> 本文是执行计划，不是完成报告。代码写完、在本地测试通过、在真实生产环境运行成功，是三件不同的事。本文明确区分这三种状态；没有真实发生的部署、观察和用户验证，一律不写成“已完成”。

## 1. 一句话目标

P0 要先把 Standard Astro 变成一个可以安全恢复、不会把不可靠数字写进普通回答、并能在生产环境稳定运行的研究 Alpha。P1 再加入专门的 Claim Audit（主张审计）、可验证 Evidence Pack（证据包）、邀请与隐私控制、DESI DR2 证据矩阵，以及 Rubin、Euclid、Roman 的数据格式适配层。

AI 可以把写代码和测试的时间压缩到约 24 天，但不能压缩下列真实时间：

- 生产切换后的连续 72 小时观察；
- Claim Audit 开启前的连续 14 天 Daily 通过记录；
- 真实用户是否在 28 天内返回使用。

因此，本计划有三个不同的完成时间：

| 里程碑 | 最早目标 | “完成”的真实含义 |
|---|---:|---|
| P0 工程和生产门槛 | 第 10–13 天 | 代码、迁移、恢复、切换和 72 小时生产观察全部通过 |
| P1 工程 | 第 18–24 天 | P1 代码和测试完成，但功能开关仍可保持关闭 |
| P1 产品验证 | 第 7–8 周 | 至少 28 天的真实用户返回数据达到门槛 |

## 2. 先学会看状态

本文使用四种状态，避免把“有代码”误写成“已上线”：

| 标记 | 中文 | English | 含义 |
|---|---|---|---|
| ✅ | 已实现代码 | code implemented | 当前代码分支里已经存在实现 |
| 🧪 | 已本地验证 | locally verified | 在本地或隔离测试环境执行过，证据可复查 |
| ⏳ | 外部门槛待完成 | external gate pending | 需要云资源、真实密钥、真实流量、连续时间或用户参与 |
| ⛔ | 未实现或未证明 | not implemented / not demonstrated | 当前基线中没有实现，或没有足够证据证明 |

### 2.1 当前真实状态快照

这是 2026-07-17 当前实施分支的快照，不代表 GitHub `main` 已合并，也不代表 Render 已部署。

| 范围 | 当前状态 | 已有证据 | 仍缺什么 |
|---|---|---|---|
| Daily B4/B5 数字洗白防护 | ✅ 🧪 | 最终回答边界会删除用户粘贴但未被本轮工具复算的数字；聚焦测试通过 | 合并、部署、每项连续 5 次真实模型通过 |
| Daily F2 非正式后验值 | ✅ 🧪 | `publication_ready=false` 或 `__do_not_claim__=true` 时，后验数字只保留在工具结果，不进入普通回答 | 真实模型连续运行和生产 Daily 记录 |
| Daily C2 数据覆盖范围 | ✅ 🧪 | registry 返回结构化 `coverage_status`，回答会说明超出测量范围 | 真实模型连续运行和生产 Daily 记录 |
| A1 与 compressed chain 规则 | ✅ 🧪 | A1 已与 F2 的 `WITHHELD` 规则对齐，不再要求同一条探索链输出可发表 H0 | 真实 Daily 重新建立绿色基线 |
| Daily 失败分类 | ✅ 🧪 | 输出 `verdicts.json`，区分产品缺陷、评估器误报、模型漂移、外部依赖和 CI 基础设施 | 接入长期 Daily 台账和告警 |
| 本地工程验证 | ✅ 🧪 | 完整后端为 3398 passed、7 skipped、59 deselected，覆盖率 67.80%；前端 203/203、lint、production build 和迁移往返均通过 | 仍需 GitHub CI 保存正式 artifact；本地通过不等于生产通过 |
| Daily 真实模型验证 | ⏳ | 本地 CLI 失败被正确分类为 `external_dependency` | 当前沙箱不允许把仓库提示词发送到外部模型；需 CI 密钥或用户明确批准 |
| Render 目标拓扑 | ✅ 🧪 | [`render.yaml`](../../render.yaml)、readiness/deep health、Worker/Beat commit 检查和相关测试已存在 | 未证明目标 Blueprint 已用真实资源同步并稳定运行 |
| 备份和恢复代码 | ✅ 🧪 | [`backup.sh`](../../backend/scripts/ops/backup.sh)、[`restore.sh`](../../backend/scripts/ops/restore.sh) 和 CI smoke test 已存在 | 旧生产数据的真实完整备份、隔离恢复和恢复验收未完成 |
| 生产切换 | ⏳ | 已有英文[切换清单](../PRODUCTION_CUTOVER_CHECKLIST.md)和本计划配套 Runbook | 付费资源、密钥、维护窗口、最终同步和流量切换均未执行 |
| 72 小时观察 | ⏳ | 已定义检查项和记录模板 | 尚未开始，不能提前算作完成 |
| 严格 DESI v1 收尾 | ⏳ | 有冻结协议、分析和分级代码基础 | 仍需在绑定的原路径和 exact 环境完成正式运行、独立复算和 Evidence Pack；最终必须保持 `WITHHELD` |
| Apache-2.0 与 DCO | ✅ 🧪 | 根目录已有 Apache-2.0 [`LICENSE`](../../LICENSE)，并补充 [`DCO.md`](../DCO.md)、[`DATA_LICENSES.md`](../DATA_LICENSES.md) 和贡献说明 | 合并后由 GitHub/发布页展示，外部数据仍按各自许可证 |
| Claim Audit / P1 Evidence Pack | ✅ 🧪 | 已有顶层模型、独立运行/科学状态、owner-scoped API、Celery lease、4 文件 Evidence Pack、验签/篡改/密钥轮换和专用前端 | 完整 CI、生产暗发布及 14 天 Daily 门；`SUPPORTED` 仍不等于同行评议 |
| 注册邀请码 / invite-only signup、分析同意、整户删除 | ✅ 🧪 | 已实现 keyed-hash 一次性邀请、fragment 安全链接、显式 analytics 同意、30 天访问边界、账号立即停用、外部 tombstone、全部对象版本 fail-closed 删除，以及带 24 小时宽限/五分钟清理的持久孤儿产物队列 | 仍需 PostgreSQL 并发演练、真实 S3/R2 多版本与 delete-marker 删除权限、无逾期清理队列和生产恢复验证 |
| DESI DR2 官方 posterior chains | ✅ 🧪 | 官方镜像 registry 固定三组 SN 组合、下载地址/SHA/参数/权重/诊断；中心与区间回归通过，Tension Lab 对共享数据拒绝简单 sigma | 严格 v1 是另一条待完成门；DR2 汇总保持非 publication-ready |
| Rubin/Euclid/Roman adapter | ✅ 🧪 | 三套 checksum-bound `SurveyProductAdapter` fixture 和 drift/fail-closed 测试已实现 | 均保持 `SCHEMA_FIXTURE_ONLY`；没有版本固定正式数据前不得升级为可执行 |
| 14 天 Daily / 28 天用户验证 | ⏳ | 门槛已定义 | 都尚未完成；机器人、AI 自测和 GitHub clone 不能计为用户 |

本次工程实现已保存为提交 `7981de3c6a80b2e18a3eced6ace04a472fd35436`；如果之后被 cherry-pick 或 squash，发布记录必须改用最终合并后的完整 Git SHA。

### 2.2 本地验证收据

下面是一次本地运行记录，不是 CI artifact，也不是生产验收。日志仅存在于本次代理终端，因此合并后仍需由 CI 重跑并保存 artifact。

| 日期 | 代码基线 | 命令 | 结果 | 限制 |
|---|---|---|---|---|
| 2026-07-17 | `7981de3c6a80b2e18a3eced6ace04a472fd35436` | `ruff check app tests alembic celery_worker.py` | passed | 本地 supported venv；仓库全量 `ruff check .` 仍有既有 spike 脚本 E402，不属于运行代码范围 |
| 2026-07-17 | 同上 | `pytest tests -q` | 3398 passed、7 skipped、59 deselected；coverage 67.80% | 为精确管线测试，从原项目严格运行区复制了被 Git 忽略的 Pantheon+ 表和官方 STATONLY 协方差；两者未加入提交，也不代表严格 DESI v1 已完成 |
| 2026-07-17 | 同上 | `npm test -- --run`、`npm run lint`、production build | 203/203 passed；lint/build passed | build 保留既有 Plotly 大 chunk 警告，不是构建失败 |
| 2026-07-17 | 同上 | Alembic fresh upgrade → downgrade `6a718293b4c5` → upgrade head；legacy 缺列 bridge upgrade | passed | SQLite 隔离演练；真实 PostgreSQL 克隆和生产恢复仍是发布门 |
| 2026-07-17 | 历史 Daily-only baseline `e18ddf8a2e01cf507b7756c4547ee1513497edea` | Daily local CLI：A1/B4/B5/C2/F2 | 5 个 `ERROR`，均分类为 `external_dependency` | 沙箱阻止 CLI；没有产生可计入验收的科学运行 |

重跑时使用项目支持的 backend venv；机器绝对路径不是发布合同。CI artifact 应至少保存命令、完整 SHA、依赖环境、stdout/stderr、JUnit/coverage 和 Daily `verdicts.json`。

## 3. 本科生版关键术语

| 中文 | English | 简单解释 |
|---|---|---|
| 主张 | claim | 一句准备让读者相信的话，例如“数据支持某个 H0 数值” |
| 主张审计 | Claim Audit | 把一句主张拆开，逐项检查它是否真的有证据 |
| 证据包 | Evidence Pack | 可下载、可验签、可复查的一组报告、引用和运行记录 |
| 当前运行证据 | current-run evidence | 这一次请求里由服务器真实执行得到的结果，不是用户粘贴的旧文字 |
| 可发表就绪 | publication-ready | 达到本项目预先定义的诊断和溯源门槛；不等于已经同行评议 |
| 暂缓结论 | WITHHELD | 有一些结果，但证据不够，所以不允许给出强结论 |
| 能力缺口 | CAPABILITY_GAP | 缺数据、likelihood、schema 或工具，当前系统做不了 |
| 失败关闭 | fail closed | 不确定时选择不给强结论，而不是猜一个结果 |
| 后验分布 | posterior | 用数据更新模型参数后得到的概率分布 |
| R-hat | R-hat convergence diagnostic | 多条采样链是否收敛到相同分布的指标；越接近 1 越好 |
| 有效样本量 | ESS, effective sample size | 虽然有很多采样点，但真正相当于多少个独立信息点 |
| 蒙特卡洛误差 | MCSE | 因有限采样本身带来的数值误差 |
| 哈希 | SHA-256 checksum | 文件的“数字指纹”；文件改一个字节，指纹通常就会变 |
| 数据库迁移 | database migration | 受版本控制的数据库结构变更 |
| 盖章 | Alembic stamp | 只记录“数据库处于哪个版本”，不会自动修复结构 |
| 桥接迁移 | bridge migration | 当旧数据库结构与预期不同，用显式迁移把差异安全补齐 |
| 时间点恢复 | PITR, point-in-time recovery | 把数据库恢复到过去某个时间点的新实例 |
| 自带密钥 | BYOK, bring your own key | 用户提供自己的模型服务密钥，平台不替用户付模型费用 |
| 工作进程 | Celery Worker | 从队列拿任务并执行科研计算的后台进程 |
| 定时器 | Celery Beat | 按计划把任务放入队列的后台调度器 |
| 功能开关 | feature flag | 代码已部署但先保持关闭，满足门槛后再打开 |
| 流量切换 | cutover | 把真实用户请求从旧环境改到新环境 |
| 回滚 | rollback | 新环境有问题时，把流量切回已验证的旧资源或恢复实例 |
| 恢复点目标 | RPO, recovery point objective | 最多能接受丢失多久的数据；目标为 0 不代表实际一定为 0 |
| 恢复时间目标 | RTO, recovery time objective | 从故障到恢复服务希望不超过多久 |
| 数据结构 | schema | 数据库有哪些表、列、类型、约束和索引 |
| 清单 | manifest | 机器可读的文件、版本、哈希和运行信息目录 |
| 发布身份 | release identity | 一次发布的完整 Git SHA、版本和构建信息 |
| 数据追平 | reconciliation | 比较两个系统的差异，并安全补齐缺失或冲突的数据 |
| 租约 | lease | Beat 定期续期的“我仍在负责调度”记录，过期说明调度器可能失效 |
| 冒烟测试 | smoke test | 用一条最小真实路径快速确认系统基本能工作 |
| 注册表 | registry | 服务器认可的数据集、版本、范围和规则目录 |
| 似然 | likelihood | 在给定模型参数时，观测数据出现得有多合理的数学函数 |
| 协方差 | covariance | 不同测量误差如何一起变化；忽略它可能夸大显著性 |
| 张力显著性 | tension sigma | 两个结果相差多少个标准差；共享数据时不能用简单公式 |
| 不支持的科学结论逃逸 | unsupported scientific escape | 没有足够服务器证据的强结论仍然出现在用户可见回答中 |
| A-ready | research-grade readiness | 项目最高证据准备等级；不是“发现已被学界确认” |
| 开发者来源证书 | DCO, Developer Certificate of Origin | 贡献者声明自己有权提交这份代码或内容的流程 |

## 4. AI 并行开发方式

三个工作流并行，主代理负责最后的合并和发布判断。

| 工作流 | 主要责任 | 不得越界的部分 |
|---|---|---|
| A：可靠性与运维 | Daily、数据库、Render、备份恢复、健康检查 | 不修改严格 DESI 运行目录 |
| B：科学证据 | 严格 DESI v1、DR2 chains、Evidence Matrix、Tension Lab | 独占严格科学运行原路径；不执行生产流量切换 |
| C：产品与隐私 | Claim Audit、Evidence Pack、前端、邀请、删除、产品指标 | 不放宽科学证据门，也不读取不需要的用户内容 |
| 主代理 | 冻结接口、代码审查、冲突处理、集成测试、合并、发布建议 | 未经用户批准不创建付费资源、不切生产流量、不邀请外部用户 |

执行规则：

1. 每个工作流使用独立 Git worktree 和分支。
2. 每个 PR 只处理一个主题，便于撤回和审查。
3. Day 0 先冻结公共接口、数据库字段和验收条件。
4. P1 可以提前开发，但 P0 未通过时必须由功能开关保持关闭。
5. 严格 DESI 运行所在原路径只允许科学工作流写入。
6. 所有生成文件先写到临时目录，验完哈希再发布到正式目录。
7. AI 可以写代码、测试和文档；生产切换、付费资源、密钥、外部邀请仍需用户批准。

## 5. 依赖关系：什么能并行，什么必须等待

```text
Day 0 安全基线
├─ A: Daily + 运维准备 ──> 隔离恢复 ──> 平行 Render 栈 ──> 生产切换 ──> 72h
├─ B: 严格 DESI v1 ─────> v1 Evidence Pack（固定 WITHHELD）
└─ C: P1 模型/隐私骨架 ─> Claim Audit + Evidence Pack + 前端
                              │
P0 完成 + Daily 连续 14 天 ───┴─> 开启 Claim Audit Alpha
                                       │
                                       └─> 28 天真实用户验证
```

必须等待的门：

- 没有可恢复基线，不能迁移生产数据库。
- 没有隔离恢复成功，不能切流量。
- P0 没有经过 72 小时，不能称为 P0 完成。
- Daily 没有连续 14 天通过，不能开启 Claim Audit。
- 没有 28 天真实用户返回数据，不能称为 P1 产品验证完成。

## 6. 压缩后的总日程

### Day 0：建立可恢复的安全基线

目标：任何 AI 分支出错时，都能回到同一个明确起点。

- 从执行时最新的 `origin/main` 创建工作分支。
- 保存分支、完整 Git SHA、工作树状态、依赖锁文件哈希和 Alembic heads。
- 为严格 DESI chains、配置、收据、答案键隔离文件和协议生成 SHA-256 清单。
- 把科学运行数据复制到独立备份位置；不能只放在同一块磁盘。
- 冻结公共 API、数据库模型、状态枚举、Evidence Pack manifest 和验收条件。
- 填写[生产执行记录模板](../runbooks/P0_PRODUCTION_RECORD_TEMPLATE.zh-CN.md)的基线部分。

完成条件：代码、数据库和严格科学运行数据都有明确恢复路径。仅有“备份命令成功”不算完成，至少要能在隔离位置验证清单。

### Day 1–3：三个 P0 工作流并行

#### A：修复并验证 Daily

代码目标：

- F2：`publication_ready=false` 或 `__do_not_claim__=true` 时禁止普通回答输出后验数值。
- B4/B5：拒绝用户提供的假结果时，不复述假数字。
- C2：输出结构化 `coverage_status`。
- A1：删除与 compressed-chain `WITHHELD` 规则矛盾的旧期望。
- 输出机器可读失败分类。

当前状态：这些代码已在本地分支实现并通过聚焦测试，但真实模型五连和完整 Daily 三连尚未完成。

验收：

- B4、B5、C2、F2 各自连续运行 5 次通过。
- 完整 Daily 连续 3 次通过。
- 每次运行保存结果、完整 Git SHA、provider/model、case verdict 和 `failure_class`。
- provider timeout 或网络错误不能算科学通过，也不能伪装成产品缺陷。

#### B：严格 DESI v1 收尾

- 在原路径临时切换到运行绑定的 `b308def`。
- 使用绑定的 exact 环境运行 canonical analysis。
- 检查 R-hat、ESS、MCSE、链平衡、输入和输出哈希。
- 使用第二套已有后处理代码独立复算区间。
- 生成以下包：

```text
manifest.json
analysis.json
independent_analysis.json
grade.json
gap_report.md
artifact_hashes.json
```

- 最终科学状态固定为 `WITHHELD`。
- 即使链诊断通过，也不允许写成 A-ready、分析者盲法已实现或“动态暗能量发现”。

#### C：治理与生产准备

- 选择并添加 Apache-2.0 根许可证。
- 添加 DCO 流程，并明确签署方式。
- 盘点所有外部数据产品的许可证和致谢要求。
- 把现有 `PRIVACY.md` 从参考实现说明扩展成真实部署可填写框架。
- 盘点 Render 服务、PostgreSQL、Redis、Worker、Beat、密钥、磁盘和对象存储。
- 准备迁移、恢复、切换和回滚记录。

当前实施分支已加入 Apache-2.0、DCO 与外部数据许可清单；是否进入 GitHub `main` 仍以最终合并提交为准。

### Day 4–7：隔离恢复和新生产环境

运维工作流按照[配套 Runbook](../runbooks/P0_PRODUCTION_OPERATIONS_RUNBOOK.zh-CN.md)执行：

1. 对旧数据库和对象存储做完整、带哈希的备份。
2. 恢复到全新的隔离数据库和全新对象路径。
3. 如果旧库没有 `alembic_version`，先比较完整 schema；一致才允许在克隆库 stamp，有差异就写 bridge migration。
4. 建立平行 Render 栈：PostgreSQL 16、Redis `noeviction`、Backend、Worker、Beat、Frontend、持久磁盘和开启版本控制的 S3/R2。
5. 验证 `/health/ready`、`/health/deep`、完整 commit、一条 Worker 科研任务、BYOK、匿名付费密钥阻断、登录、历史会话、加密密钥和证据签名。

同期允许 P1 在独立分支开发：

- `ClaimAudit` 和 `EvidencePack` 数据模型；
- 邀请模式和隐私偏好；
- DESI DR2 官方 chain registry；
- Claim Audit 前端页面骨架。

这些功能此时必须关闭，不能影响 P0 切换。

### Day 8：最多 30 分钟的生产切换

- 进入批准的写入冻结窗口。
- 等待或明确终止运行中任务，禁止静默遗弃。
- 从同一个一致性窗口完成数据库和对象存储最终同步。
- 同时切换 API、Frontend、Worker、Beat、数据库和存储。
- 计划数据丢失量是 0；切换后要用记录证明实际值，不能只写目标。
- 旧环境立即改为只读，但先不删除。

任何超时、数据不一致或健康检查失败都触发回滚，而不是延长一个失控的维护窗口。

### Day 8–11：连续 72 小时生产观察

持续监控：

- readiness/deep health；
- PostgreSQL 和 Redis 延迟；
- Worker 队列、失败和 stale tasks；
- Beat lease 和 commit；
- 对象 checksum；
- 证据签名和历史 key 验证；
- 登录、BYOK、匿名付费路径阻断和错误率；
- Daily 结果。

如果 readiness 失败、服务 commit 不一致、数据错误或科学证据回归：

- 先全局冻结写入并快照 target，记录 target 已确认的最后写入 watermark；
- 旧资源或 PITR 克隆保持只读，完成 target 新写入的差异核对和幂等反向同步后，才允许作为回滚目标；
- API、Frontend、Worker、Beat、数据库和存储一起回退，并由数据库复核人和发布负责人双人批准恢复写入；
- 不直接对生产做临时 Alembic downgrade；
- 修复后重新开始完整 72 小时观察。

正常切换的写冻结预算是 30 分钟。已经恢复用户写入后若在 72 小时内发生事故，安全回滚可能需要更长的 incident freeze；此时零丢失和一致性优先，不能为满足 30 分钟数字而跳过 reconciliation。

72 小时无回归后，才可以：

- 停止旧计算资源；
- 旧数据库快照和对象备份至少保留 30 天；
- 发布 `v0.4.0-alpha.1`；
- 把 P0 标记为完成。

当前状态：72 小时观察尚未开始。

## 7. P1 工程：Day 8–24

### P1.1 邀请与隐私：Day 8–12

生产默认值：

```text
SIGNUP_MODE=invite_only
SHARED_DEEPSEEK_API_KEY_ENABLED=false
CLAIM_AUDIT_ENABLED=false
```

实现要求：

- 邀请码只保存哈希，原始值只显示一次。
- 兑换邀请时创建正常用户名和密码；已兑换码不能作为登录凭证。
- 旧 SetupKey 用户通过一次性迁移邀请转换。
- 用户可同意或拒绝产品分析。
- 产品分析事件保留 30 天。
- analytics 禁止保存 claim、prompt、论文标题、URL、DOI、工具参数、错误原文和科研数值。
- 研究记录和 Evidence Pack 保留到用户删除。
- `DELETE /api/auth/account` 立即停用账号，再异步清理关联数据。
- deletion tombstone 防止旧备份把已删除账号重新激活。

### P1.2 Claim Audit 后端：Day 8–14

新增顶层 `ClaimAudit`；现有 `ResearchJob` 作为子任务。

运行状态和科学状态必须分开：

```text
运行状态 / execution status
QUEUED | RUNNING | COMPLETED | FAILED_RETRYABLE | FAILED_FINAL | CANCELLED

科学状态 / scientific status
SUPPORTED | WITHHELD | CAPABILITY_GAP
```

最重要的区别：provider timeout 是 `FAILED_RETRYABLE`，不是科学能力缺口；执行成功也不自动等于 `SUPPORTED`。

科学聚合规则：

- 只有每个强主张都有服务器可验证的完整证据路径，顶层才可为 `SUPPORTED`。
- 任一数值缺少本轮产生的、`publication_ready=true` 的服务器证据，顶层必须为 `WITHHELD`。
- 缺少已注册数据、likelihood 或 schema 时，顶层为 `CAPABILITY_GAP`。
- 一个 Audit 可以技术上 `COMPLETED`，同时科学上是 `WITHHELD` 或 `CAPABILITY_GAP`。

固定流程：

1. 拆分和规范化 claim。
2. 解析 DOI、arXiv、Bibcode 或白名单 URL。
3. 检查 registry 覆盖。
4. 运行受控工具。
5. 只用服务器签名记录建立 evidence graph。
6. 检查每个 claim 的证据路径。
7. 聚合 scientific verdict。
8. 生成 Evidence Pack。
9. 只记录经过过滤的产品事件。

公共接口：

```text
POST   /api/research/claim-audits
GET    /api/research/claim-audits
GET    /api/research/claim-audits/{audit_id}
POST   /api/research/claim-audits/{audit_id}/cancel
POST   /api/research/claim-audits/{audit_id}/retry
DELETE /api/research/claim-audits/{audit_id}

GET    /api/research/evidence-packs/{pack_id}/download
POST   /api/research/evidence-packs/verify

PUT    /api/privacy/preferences
DELETE /api/auth/account
```

信任边界：客户端传来的 tool result、evidence graph 和 `publication_ready=true` 一律不可信，必须由服务器重新绑定和验签。

### P1.3 Evidence Pack 与前端：Day 12–17

最小 Evidence Pack：

```text
manifest.json
report.md
citations.bib
provenance.json
```

Manifest 至少包含：audit/owner/time、release/commit、输入和结果哈希、normalized claims、逐 claim verdict、完整证据路径、数据和 likelihood 版本、工具/config/seed/diagnostics、limitations、capability gaps、pack hash、签名和 key id。

规则：

- 未 finalized 不能下载。
- 默认私有，并进行 owner isolation。
- 修改任一文件后验证失败。
- key 轮换后旧包仍可通过 retained key 验证。
- `WITHHELD` 和 `CAPABILITY_GAP` 也可导出，但必须醒目标记。
- `SUPPORTED` 只表示达到平台证据门，不等于同行评议。

前端需要输入 claim/source、选择 `audit_only` 或 `execute_registered`、显示任务状态、逐 claim 证据、Evidence Graph、下载/验证和历史恢复。

### P1.4 DESI DR2 Matrix：Day 13–19

当前实施分支已基于 [DESI DR2 官方 chains 发布页](https://www.desi.lbl.gov/2025/10/06/desi-dr2-cosmology-chains-and-data-products-released/)实现 posterior-chain 组合 registry；以下内容保留为发布验收合同：

- 官方下载地址和固定版本；
- 论文、数据组合和模型；
- 参数映射；
- burn-in 和 weight 规则；
- 每个文件 SHA-256；
- 许可证、致谢和 evidence tier。

现有矩阵增加：

```text
bao_dataset_key = desi_dr1_bao | desi_dr2_bao
```

默认仍为 DR1，保持兼容。新增：

```text
run_dark_energy_evidence_matrix(
  model = lcdm | wcdm | w0wa_cdm,
  supernova_sets = pantheon_plus | union3 | des_sn5yr,
  include_desi_dr1_reference = false
)
```

Tension Lab 必须知道三组结果共享 DESI/CMB，因此没有 cross-covariance 时不得计算简单 tension sigma；返回 `correlated_tension_withheld`，只比较中心、区间和二维轮廓。官方显著性只作为带引用的文献背景。

验收：中心值与官方表格偏差不超过 `0.1σ`，区间宽度差异不超过 5%；checksum、参数名、weight 或组合错误时 fail closed；DR1 与 DR2 不进入同一个 likelihood。

执行时重新检查官方来源和版本，不把计划中的 URL 当成永远不变的事实。

### P1.5 Rubin、Euclid、Roman Schema：Day 16–21

建立 `SurveyProductAdapter`，至少描述 release/schema version、字段和单位、坐标系、时间系统、redshift 类型、covariance、mask、selection、coverage/checksum、auth/rate limit/license 和 supported claim scope。

P1 只要求：

```text
SCHEMA_FIXTURE_ONLY
```

只有执行时已有正式、固定版本的数据，才可升级为 `SOURCE_PINNED`。P1 不要求在线查询或 `EXECUTABLE`，不能为了进度虚构数据可用性。

执行时重新核查官方入口：[Rubin Early DP2](https://rubinobservatory.org/events/edp2-release)、[Euclid 官方时间表](https://www.cosmos.esa.int/web/euclid/timeline)和 [NASA Roman 状态](https://science.nasa.gov/missions/roman-space-telescope/building-roman/)。这些链接是核查入口，不代表对应数据已满足 `SOURCE_PINNED`。

### P1.6 集成与发布：Day 20–24

- 完整 backend 测试；
- frontend test、lint、build；
- database upgrade/downgrade 演练，但 downgrade 不直接作用于生产；
- Claim Audit E2E；
- Evidence Pack 篡改和旧 key 验证；
- owner isolation；
- 服务重启恢复；
- provider timeout、archive outage；
- DR2 overlap/checksum/weight；
- survey schema drift；
- 历史 Daily 和 red-team 回归。

工程通过后可发布 `v0.5.0-alpha.1`，但 `CLAIM_AUDIT_ENABLED=false`，直到 Daily 连续 14 天通过。

## 8. Alpha 和不能压缩的时间

### Week 2：提前招募但不提前计数

- 联系 3–5 位设计伙伴，预约 Week 3–4。
- 准备 DESI/BAO、SN/H0、CMB/S8 三类任务。
- 每人准备两个真实主张。
- 只有用户批准后才发送外部邀请。

提前联系不等于用户已经完成 Audit，也不能计入 28 天返回率。

### Week 4：满足门槛后才开启 Claim Audit

同时满足：

- P0 已完成；
- Daily 连续 14 天通过；
- readiness/deep 稳定；
- 隐私、Evidence Pack 和删除已验证，其中真实版本化对象存储的多版本/delete-marker 删除测试通过，且没有逾期 `artifact_cleanup_queue`；
- 固定回归任务全部通过。

### Week 4–5：3–5 位设计伙伴

- 使用真实任务；
- 每周汇总失败点；
- AI 在 24–48 小时内做小版本修正；
- 不为提高成功率而放宽证据门。

### Week 5–6：扩大到 10–15 人

经用户批准后，通过 GitHub Discussion、公开科研社区和定向邀请扩展。

### Week 7–8：最早检查 28 天验证门

产品门槛：

- 至少 10 人完成首次 Audit；
- 至少 5 人在 28 天内返回；
- 至少 3 人完成两个以上独立 workflow；
- 至少 20 个真实 workflow；
- 至少 50% 产生 `SUPPORTED` 或可行动的 `CAPABILITY_GAP`；
- 0 次 unsupported scientific escape；
- `/health/ready` 可用率至少 99%；
- 固定演示任务首次得到证据的 P95 不超过 10 分钟。

如果工程代码完成但人数不足，唯一正确写法是：

> P1 engineering complete；product validation pending recruitment。

## 9. 发布门和最终完成定义

### P0 完成门

必须全部满足：

- [ ] B4、B5、C2、F2 各连续 5 次通过。
- [ ] 完整 Daily 连续 3 次通过。
- [ ] 严格 DESI v1 已分析、独立复算并诚实保持 `WITHHELD`。
- [ ] `/health/ready` 和 `/health/deep` 在真实目标环境均为 200。
- [ ] Backend、Worker、Beat 报告同一完整 Git SHA。
- [ ] 真实备份恢复、BYOK、Worker 和证据签名通过。
- [ ] 新生产环境连续观察 72 小时无回归。
- [ ] Apache-2.0、DCO、数据许可清单、真实部署隐私说明和 prerelease 已发布。

当前不能勾选 P0 完成。

### P1 工程完成门

- [ ] Claim Audit 专用工作流可用。
- [ ] Evidence Pack 可生成、验签、下载和删除。
- [ ] 邀请、隐私偏好和账户删除可用。
- [ ] DESI DR2 Matrix 与 Tension Lab 通过科学回归。
- [ ] Rubin、Euclid、Roman 达到 Schema Fixture 就绪。
- [ ] backend、frontend、migration、E2E、安全和红队测试通过。

当前不能勾选 P1 工程完成。

### P1 产品验证门

- [ ] 真实用户数量、workflow 数和 28 天返回率达到 Week 7–8 门槛。
- [ ] 机器人测试、AI 自测、GitHub clone 均未计入真实用户。
- [ ] 0 次 unsupported scientific escape。

当前不能勾选 P1 产品验证完成。

## 10. 每日主代理检查清单

主代理每天只需要回答以下问题：

1. 今天合并了哪些单主题 PR？
2. 公共接口或数据库字段是否发生未冻结的分叉？
3. 是否有测试失败被错误归类成“科学缺口”？
4. 是否出现客户端证据被当成可信证据？
5. 严格 DESI 原路径是否仍由科学工作流独占？
6. P1 功能是否仍由开关关闭？
7. 是否有需要用户批准的付费资源、密钥、流量或外部邀请？
8. 72h、14d、28d 的时钟是否真实推进，还是仅仅代码变绿？

## 11. 权威文件和复用入口

不要在聊天记录里临时发明第二套运维流程。执行时按以下顺序阅读：

1. 本路线图：范围、时间、依赖和完成定义。
2. [P0 中文生产 Runbook](../runbooks/P0_PRODUCTION_OPERATIONS_RUNBOOK.zh-CN.md)：本轮具体执行顺序。
3. [P0 可填写记录模板](../runbooks/P0_PRODUCTION_RECORD_TEMPLATE.zh-CN.md)：留下证据。
4. [英文 Operations Runbook](../OPERATIONS_RUNBOOK.md)：现有脚本的完整安全合同。
5. [Production Cutover Checklist](../PRODUCTION_CUTOVER_CHECKLIST.md)：生产授权边界。
6. [`render.yaml`](../../render.yaml)：目标拓扑声明。
7. [`backend/scripts/ops/backup.sh`](../../backend/scripts/ops/backup.sh) 和 [`restore.sh`](../../backend/scripts/ops/restore.sh)：可移植备份和 fail-closed 恢复。
8. [`backend/scripts/verify_deployment.py`](../../backend/scripts/verify_deployment.py)：目标部署健康和 commit 验证。
9. [`backend/scripts/daily_blind.sh`](../../backend/scripts/daily_blind.sh)：Daily 真实聊天路径。

当本文和更底层安全合同冲突时，选择更严格、会 fail closed 的要求，并在 PR 中修正文档差异。
