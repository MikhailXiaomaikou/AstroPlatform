# Standard Astro P0 生产执行记录模板

- 用途：复制本文件作为一次具体发布的审计记录。
- 建议文件名：`P0_PRODUCTION_RECORD_<YYYY-MM-DD>_<short-sha>.md`
- 配套 Runbook：[P0 生产迁移与观察 Runbook](./P0_PRODUCTION_OPERATIONS_RUNBOOK.zh-CN.md)

> 本模板中的空白、`[ ]` 和 `待填写` 都表示尚未完成。不要预先勾选，不要把目标值复制成实际值，不要把 secret、数据库 URL 或 API key 写进本文件。

## 1. 发布授权

| 字段 | 填写内容 |
|---|---|
| 记录 ID | 待填写 |
| 计划 release/tag | 待填写 |
| 目标完整 Git SHA | 待填写（40 位） |
| 发布分支 | 待填写 |
| 操作员 | 待填写 |
| 数据库复核人 | 待填写 |
| 科学复核人 | 待填写 |
| 回滚负责人 | 待填写 |
| 用户/发布批准人 | 待填写 |
| 维护窗口（含时区） | 待填写 |
| 正常切换写冻结预算 | 30 分钟；到点必须完成或进入 incident rollback |
| 批准的预算/资源 | 待填写 |
| 目标 region/capacity | 待填写 |
| 计划 RPO | 0；这是目标，不是实际结果 |
| 计划 RTO | 待填写 |
| 外部邀请是否批准 | 是 / 否 / 不适用 |

授权：

- [ ] 付费 Render 资源已批准。
- [ ] 生产密钥配置已批准。
- [ ] 维护窗口已批准。
- [ ] 流量切换已批准。
- [ ] 回滚负责人已在线。
- [ ] 删除旧资源尚未授权，需 72h 后单独批准。

批准证据链接：待填写

## 2. 安全基线记录

### 2.1 Git 和依赖

| 项目 | 值 | 验证人 |
|---|---|---|
| `origin/main` SHA | 待填写 | 待填写 |
| 发布 SHA | 待填写 | 待填写 |
| `git status --short` | 待填写 | 待填写 |
| Alembic heads | 待填写 | 待填写 |
| `requirements.lock` SHA-256 | 待填写 | 待填写 |
| frontend lockfile SHA-256 | 待填写 | 待填写 |
| `render.yaml` SHA-256 | 待填写 | 待填写 |
| baseline manifest SHA-256 | 待填写 | 待填写 |

### 2.2 严格 DESI 文件清单

| 相对路径/逻辑名 | 字节数 | SHA-256 | 备份位置 | 抽查结果 |
|---|---:|---|---|---|
| canonical config | 待填写 | 待填写 | 待填写 | 待填写 |
| frozen protocol | 待填写 | 待填写 | 待填写 | 待填写 |
| amendment | 待填写 | 待填写 | 待填写 | 待填写 |
| chain 1 | 待填写 | 待填写 | 待填写 | 待填写 |
| chain 2 | 待填写 | 待填写 | 待填写 | 待填写 |
| chain 3 | 待填写 | 待填写 | 待填写 | 待填写 |
| chain 4 | 待填写 | 待填写 | 待填写 | 待填写 |
| checkpoint | 待填写 | 待填写 | 待填写 | 待填写 |
| environment receipt | 待填写 | 待填写 | 待填写 | 待填写 |
| 其他 | 待填写 | 待填写 | 待填写 | 待填写 |

- [ ] 独立备份不在源机器/源磁盘的同一故障域。
- [ ] 清单由第二人抽查。
- [ ] answer key 没有进入 generate/run/analyze 环境。

## 3. 旧生产环境盘点

### 3.1 服务和资源

| 组件 | 旧资源 | 旧 commit/version | 目标资源 | 目标 plan/region | 备注 |
|---|---|---|---|---|---|
| Backend | 待填写 | 待填写 | 待填写 | 待填写 |  |
| Frontend | 待填写 | 待填写 | 待填写 | 待填写 |  |
| Worker | 待填写 | 待填写 | 待填写 | 待填写 |  |
| Beat | 待填写 | 待填写 | 待填写 | 待填写 |  |
| PostgreSQL | 待填写 | 待填写 | 待填写 | PG 16 / 待填写 |  |
| Redis | 待填写 | 待填写 | 待填写 | `noeviction` / 待填写 |  |
| Persistent disk | 待填写 | 待填写 | 待填写 | 待填写 |  |
| S3/R2 | 待填写 | versioning: 待填写 | 待填写 | 待填写 |  |

### 3.2 密钥与敏感数据

只填写 ID 或“已托管”，不填写 secret 值。

| 项目 | ID/状态 | 外部托管位置代号 | 恢复验证 |
|---|---|---|---|
| JWT | 待填写 | 待填写 | 待填写 |
| Fernet | 待填写 | 待填写 | 待填写 |
| Evidence signing current key | 待填写 | 待填写 | 待填写 |
| Retired verification keyring | 待填写 | 待填写 | 待填写 |
| S3/R2 credential | 待填写 | 待填写 | 待填写 |

### 3.3 在途任务

| task/job ID | 状态 | 开始时间 | 处理决定 | 完成/取消时间 | 数据 reconciliation |
|---|---|---|---|---|---|
| 待填写 | 待填写 | 待填写 | 等待 / 取消 / 迁移 | 待填写 | 待填写 |

## 4. 备份收据

| 字段 | 值 |
|---|---|
| Provider PITR recovery point | 待填写 |
| Portable backup ID | 待填写 |
| Bundle 路径代号 | 待填写 |
| Bundle SHA-256 | 待填写 |
| Manifest commit | 待填写 |
| Manifest Alembic revision | 待填写 |
| Fernet key ID | 待填写 |
| Evidence signing key ID | 待填写 |
| DB backup 开始/结束 | 待填写 |
| Object consistency point | 待填写 |
| Object count / total bytes | 待填写 |
| Bucket version/snapshot ID | 待填写 |
| Disk snapshot/export ID | 待填写 |
| Offsite copy | 待填写 |

- [ ] `backup.sh` 成功。
- [ ] manifest 每个 hash 已验证。
- [ ] 备份不只存在于源资源。
- [ ] S3/R2 另有 provider-native 保护。
- [ ] 未在记录中写入 secret。

## 5. 隔离恢复演练

| 字段 | 值 |
|---|---|
| Restore database ID | 待填写 |
| Restore storage path 代号 | 待填写 |
| 使用的代码 SHA | 待填写 |
| 开始/结束时间 | 待填写 |
| 实测 RTO | 待填写 |
| 实测 RPO | 待填写 |
| `restore.sh` exit | 待填写 |
| `alembic current` | 待填写 |
| `alembic check` | 待填写 |
| `/health/ready` | 待填写 |
| `/health/deep` | 待填写 |

恢复抽查：

- [ ] 登录成功。
- [ ] 历史会话存在且 owner 正确。
- [ ] 加密 BYOK 可读但不回显 secret。
- [ ] 对象按 checksum 下载成功。
- [ ] 历史证据用 retired key 验证成功。
- [ ] 新证据用 current key 签名和验证成功。
- [ ] 恢复环境没有连接真实用户流量。

异常和处理：待填写

数据库复核签字：待填写

## 6. Schema adoption / migration 记录

| 字段 | 值 |
|---|---|
| 旧生产存在 `alembic_version` | 是 / 否 |
| Clone ID | 待填写 |
| Clone 当前 revision | 待填写 |
| 期望 revision | 待填写 |
| 完整 schema diff 工具/报告 | 待填写 |
| 表/列/约束/index diff | 无 / 待填写 |
| 是否需要 bridge migration | 是 / 否 |
| Bridge revision | 待填写 / 不适用 |
| PostgreSQL 16 drill 结果 | 待填写 |
| Dirty-data rollback 结果 | 待填写 |
| Clone stamp 命令与 revision | 待填写 / 未执行 |
| 生产 stamp 批准 | 待填写 / 不适用 |
| 生产 stamp 时间 | 待填写 / 未执行 |

安全确认：

- [ ] 未在旧生产库上试验。
- [ ] 未在 schema diff 前 stamp。
- [ ] 未用 stamp 掩盖差异。
- [ ] migration failure 可回滚且数据不变。
- [ ] Backend 是唯一 schema writer。

## 7. 平行 Render 栈验收

### 7.1 拓扑

- [ ] PostgreSQL 16。
- [ ] Redis 持久化且 `noeviction`。
- [ ] Backend。
- [ ] Celery Worker。
- [ ] Celery Beat。
- [ ] Frontend。
- [ ] Persistent disk。
- [ ] Versioned S3/R2。

### 7.2 Feature flags

| 变量 | 期望 | 实际 |
|---|---|---|
| `SHARED_DEEPSEEK_API_KEY_ENABLED` | `false` | 待填写 |
| `CLAIM_AUDIT_ENABLED` | `false` | 待填写 |
| `SIGNUP_MODE` | P1 实现前不声称生效；实现后 `invite_only` | 待填写 |
| `SANDBOX_BACKEND` | `disabled` | 待填写 |
| `ASTRO_RESEARCH_FOCUS` | `cosmology` | 待填写 |

### 7.3 健康和 smoke

| 检查 | 结果 | 证据链接/ID |
|---|---|---|
| `/health/ready` 200 | 待填写 | 待填写 |
| `/health/deep` 200 | 待填写 | 待填写 |
| Backend full SHA | 待填写 | 待填写 |
| Worker full SHA | 待填写 | 待填写 |
| Beat full SHA/lease | 待填写 | 待填写 |
| BYOK 成功 | 待填写 | 待填写 |
| 匿名付费 key 阻断 | 待填写 | 待填写 |
| Worker 真实科研任务 | 待填写 | 待填写 |
| DB/task/object 一致 | 待填写 | 待填写 |
| 登录/历史会话 | 待填写 | 待填写 |
| 历史 evidence verify | 待填写 | 待填写 |
| 新 evidence sign+verify | 待填写 | 待填写 |
| Frontend 指向 target API | 待填写 | 待填写 |

Go / No-Go 决定：待填写

决定时间：待填写

批准人：待填写

### 7.4 观察基线和阻断阈值

切换前填写。除固定硬门外，其余阈值必须有旧环境 baseline、目标上限和数据来源；空白即 No-Go。

| 指标 | 旧环境 baseline | 阻断阈值 | 数据源/查询 | 批准人 |
|---|---|---|---|---|
| `/health/ready` | 200 | 任一计划检查非 200 | 待填写 | 待填写 |
| `/health/deep` | 200 | 任一计划检查非 200 | 待填写 | 待填写 |
| release identity | 待填写 | 任一 Backend/Worker/Beat SHA 不同 | 待填写 | 待填写 |
| DB p95 latency | 待填写 | 待填写 | 待填写 | 待填写 |
| Redis p95 latency | 待填写 | 待填写 | 待填写 | 待填写 |
| HTTP 5xx/error rate | 待填写 | 待填写 | 待填写 | 待填写 |
| Queue depth | 待填写 | 待填写 | 待填写 | 待填写 |
| Oldest queued age | 待填写 | 待填写 | 待填写 | 待填写 |
| Failed/stale tasks | 待填写 | 待填写 | 待填写 | 待填写 |
| Object checksum failures | 0 | 大于 0 | 待填写 | 待填写 |
| Evidence verify failures | 0 | 大于 0 | 待填写 | 待填写 |
| Unsupported scientific escapes | 0 | 大于 0 | 待填写 | 待填写 |
| Login/BYOK/anonymous-key smoke | 全部通过 | 任一失败 | 待填写 | 待填写 |
| 最大监控缺口 | 待填写 | 超过待填写分钟，或数据无法补回 | 待填写 | 待填写 |

- [ ] 所有“待填写”阈值已完成并批准。
- [ ] 阈值在切换前冻结，事故发生后不得临时放宽。

## 8. 30 分钟切换日志

T=0 是开始写冻结；T+30 前必须完成切换验收或开始回滚。T-30 只是准备，不得提前停止用户写入。

| 相对时间 | 实际时间 | 操作 | 结果 | 操作员 | 证据/备注 |
|---|---|---|---|---|---|
| T-30 | 待填写 | 准备确认；写入仍开放 | 待填写 | 待填写 |  |
| T-10 | 待填写 | target 预热/verifier/阈值确认 | 待填写 | 待填写 |  |
| T=0 | 待填写 | 开始写冻结并停止 Beat 新调度 | 待填写 | 待填写 |  |
| T+3 | 待填写 | 处理/取消剩余在途任务 | 待填写 | 待填写 |  |
| T+5 | 待填写 | 最终 DB recovery point | 待填写 | 待填写 |  |
| T+8 | 待填写 | 最终 object sync | 待填写 | 待填写 |  |
| T+10 | 待填写 | 行数/对象/checksum 对比 | 待填写 | 待填写 |  |
| T+13 | 待填写 | target migration | 待填写 | 待填写 |  |
| T+16 | 待填写 | verifier + smoke | 待填写 | 待填写 |  |
| T+18 | 待填写 | 验证最后权威写入 watermark | 待填写 | 待填写 |  |
| T+20 | 待填写 | 原子切换；旧环境只读 | 待填写 | 待填写 |  |
| T+23 | 待填写 | health/login/queue | 待填写 | 待填写 |  |
| T+25 | 待填写 | BYOK/evidence/object | 待填写 | 待填写 |  |
| T+27 | 待填写 | latency/error/threshold | 待填写 | 待填写 |  |
| T+29 | 待填写 | final Go/rollback | 待填写 | 待填写 |  |
| T+30 | 待填写 | 结束写冻结，或已进入回滚 | 待填写 | 待填写 |  |

实际 downtime：待填写

实际 RPO：待填写

丢失/重复/待 reconciliation 项：待填写

旧环境只读时间：待填写

## 9. 回滚记录

未发生回滚时填写“不适用”，不要删除本节。

| 字段 | 值 |
|---|---|
| 是否回滚 | 是 / 否 |
| 触发时间 | 待填写 |
| 触发门槛 | 待填写 |
| 触发证据 | 待填写 |
| 决策人 | 待填写 |
| 回滚目标 | 旧资源 / PITR clone / 其他 |
| 全局写冻结确认 | 待填写 |
| Target 事故快照 ID | 待填写 |
| 切换前最后一致性点 | 待填写 |
| Target 最后权威写入 ID/时间 | 待填写 |
| Target 新对象版本范围 | 待填写 |
| 差异清单路径/哈希 | 待填写 |
| Reconciliation owner/方法 | 待填写 |
| 反向同步/重放结果 | 待填写 |
| 冲突与人工决定 | 待填写 |
| 行数/watermark/checksum 复核 | 待填写 |
| API/Frontend 回切时间（仍只读） | 待填写 |
| Worker/Beat/DB/storage 回切时间（仍只读） | 待填写 |
| 恢复写入双人批准 | 待填写 |
| 恢复写入时间 | 待填写 |
| Incident rollback 冻结开始/结束 | 待填写 |
| Incident rollback 实际冻结时长 | 待填写；安全 reconciliation 完成前不受正常 30 分钟预算约束 |
| 回滚后 health | 待填写 |
| 回滚后登录/任务/evidence | 待填写 |
| Incident 链接 | 待填写 |
| 新 72h 起点 | 待填写 |

- [ ] 未直接执行未经演练的生产 downgrade。
- [ ] 所有后台服务与 API 使用同一恢复数据库和存储。
- [ ] 在 reconciliation 完成前旧栈和 target 均未恢复写入。
- [ ] Target 已确认的新写入全部保全或逐项记录为未解决 incident。
- [ ] 数据库复核人和发布负责人双人批准恢复写入。

## 10. 连续 72 小时观察台账

开始时间：待填写

计划结束时间：待填写

实际结束时间：待填写

观察 commit：待填写

阈值表版本/签名：待填写

最大允许监控缺口：待填写分钟

| 观察点 | 时间 | ready/deep | DB/Redis | queue/worker | Beat lease/SHA | object/evidence | login/BYOK | Daily | incident | 复核人 |
|---|---|---|---|---|---|---|---|---|---|---|
| T+0 | 待填写 |  |  |  |  |  |  |  |  |  |
| T+1h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+2h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+4h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+8h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+12h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+24h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+36h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+48h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+60h | 待填写 |  |  |  |  |  |  |  |  |  |
| T+72h | 待填写 |  |  |  |  |  |  |  |  |  |

- [ ] 72 小时连续完成。
- [ ] 中间没有回滚或换 release；若有，已重新计时。
- [ ] 没有超过阈值的监控缺口；所有较短缺口均已从权威来源补回。
- [ ] 没有硬阻断项，也没有超过预先批准阈值。
- [ ] 旧 DB snapshot 和对象备份将保留至少 30 天。
- [ ] 停止旧计算资源得到单独批准。

72h 结论：待填写，默认 `PENDING`

复核人：待填写

## 11. Daily 门槛记录

### 11.1 P0 定向五连

每次运行一行；只写“pass”而没有 artifact 和 `verdicts.json` 不计数。

| Case | 次数 | 日期/时区 | Run ID | 完整 SHA | provider/model | artifact/verdicts | verdict | failure class | 有效 PASS |
|---|---:|---|---|---|---|---|---|---|---|
| B4 | 1 |  |  |  |  |  | PENDING |  | 否 |
| B4 | 2 |  |  |  |  |  | PENDING |  | 否 |
| B4 | 3 |  |  |  |  |  | PENDING |  | 否 |
| B4 | 4 |  |  |  |  |  | PENDING |  | 否 |
| B4 | 5 |  |  |  |  |  | PENDING |  | 否 |
| B5 | 1 |  |  |  |  |  | PENDING |  | 否 |
| B5 | 2 |  |  |  |  |  | PENDING |  | 否 |
| B5 | 3 |  |  |  |  |  | PENDING |  | 否 |
| B5 | 4 |  |  |  |  |  | PENDING |  | 否 |
| B5 | 5 |  |  |  |  |  | PENDING |  | 否 |
| C2 | 1 |  |  |  |  |  | PENDING |  | 否 |
| C2 | 2 |  |  |  |  |  | PENDING |  | 否 |
| C2 | 3 |  |  |  |  |  | PENDING |  | 否 |
| C2 | 4 |  |  |  |  |  | PENDING |  | 否 |
| C2 | 5 |  |  |  |  |  | PENDING |  | 否 |
| F2 | 1 |  |  |  |  |  | PENDING |  | 否 |
| F2 | 2 |  |  |  |  |  | PENDING |  | 否 |
| F2 | 3 |  |  |  |  |  | PENDING |  | 否 |
| F2 | 4 |  |  |  |  |  | PENDING |  | 否 |
| F2 | 5 |  |  |  |  |  | PENDING |  | 否 |

### 11.2 P0 完整 Daily 三连

| 序号 | 日期/时区 | Run ID | SHA | provider/model | artifact | verdict | failure classes |
|---:|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  | PENDING |  |
| 2 |  |  |  |  |  | PENDING |  |
| 3 |  |  |  |  |  | PENDING |  |

### 11.3 Claim Audit 开启前连续 14 天

| Day | 日期/时区 | Run ID | SHA | provider/model | 完整 suite | HARD/ERROR | artifact | 当天有效 PASS | streak |
|---:|---|---|---|---|---|---:|---|---|---:|
| 1 |  |  |  |  |  |  |  | 否 | 0 |
| 2 |  |  |  |  |  |  |  | 否 | 0 |
| 3 |  |  |  |  |  |  |  | 否 | 0 |
| 4 |  |  |  |  |  |  |  | 否 | 0 |
| 5 |  |  |  |  |  |  |  | 否 | 0 |
| 6 |  |  |  |  |  |  |  | 否 | 0 |
| 7 |  |  |  |  |  |  |  | 否 | 0 |
| 8 |  |  |  |  |  |  |  | 否 | 0 |
| 9 |  |  |  |  |  |  |  | 否 | 0 |
| 10 |  |  |  |  |  |  |  | 否 | 0 |
| 11 |  |  |  |  |  |  |  | 否 | 0 |
| 12 |  |  |  |  |  |  |  | 否 | 0 |
| 13 |  |  |  |  |  |  |  | 否 | 0 |
| 14 |  |  |  |  |  |  |  | 否 | 0 |

14d 结论：`PENDING`

`CLAIM_AUDIT_ENABLED` 当前值：待填写，默认应为 `false`

## 12. 严格 DESI v1 结论

| Artifact | 路径/ID | SHA-256 | 复核 |
|---|---|---|---|
| `manifest.json` | 待填写 | 待填写 | 待填写 |
| `analysis.json` | 待填写 | 待填写 | 待填写 |
| `independent_analysis.json` | 待填写 | 待填写 | 待填写 |
| `grade.json` | 待填写 | 待填写 | 待填写 |
| `gap_report.md` | 待填写 | 待填写 | 待填写 |
| `artifact_hashes.json` | 待填写 | 待填写 | 待填写 |

| 诊断 | 结果 |
|---|---|
| R-hat | 待填写 |
| bulk/tail ESS | 待填写 |
| MCSE | 待填写 |
| chain balance | 待填写 |
| independent interval agreement | 待填写 |
| analyst blinding | `NOT_ACHIEVED`，除非独立协议另有签名裁决 |
| 最终科学状态 | `WITHHELD` |

- [ ] 没有写成 A-ready。
- [ ] 没有写成动态暗能量发现。
- [ ] 没有用 compressed/exploratory 数值代替 full-likelihood 证据。

## 13. P0 最终签署

以下默认均未完成：

- [ ] B4/B5/C2/F2 各 5 连过。
- [ ] 完整 Daily 3 连过。
- [ ] 严格 DESI v1 包完整且 `WITHHELD`。
- [ ] 真实隔离恢复通过。
- [ ] 生产目标栈通过。
- [ ] 真实切换完成。
- [ ] 连续 72h 完成。
- [ ] Apache-2.0、DCO、数据许可和部署隐私说明完成。
- [ ] prerelease 已发布。

最终状态只能选择一个：

- [ ] `P0 COMPLETE`
- [ ] `P0 implementation in progress; production acceptance pending`
- [ ] `P0 BLOCKED`，原因：待填写

主操作员：待填写

数据库复核人：待填写

科学复核人：待填写

用户/发布批准人：待填写

签署时间和时区：待填写
