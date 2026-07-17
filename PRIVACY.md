# Standard Astro Privacy Notice / 隐私说明

> **Reference implementation / 参考实现**
> This file describes the privacy behavior implemented in this repository. It
> is not legal advice and is not automatically the privacy notice of any hosted
> instance. Each operator must publish accurate identity, contact,
> jurisdiction, subprocessor, log, backup, and user-rights information before
> inviting users.
> 本文件说明仓库中已经实现的隐私行为，不是法律意见，也不会自动成为某个线上实例的正式隐私政策。运营方必须在邀请用户前，填写并公开真实的主体、联系方式、法域、第三方服务、日志、备份和用户权利信息。

## 中文

### 1. 线上运营方必须填写

生产环境必须提供以下真实信息；不得保留示例值：

| 项目 | 生产环境配置 |
|---|---|
| 运营主体 | `PRIVACY_OPERATOR_NAME` |
| 隐私联系方式 | `PRIVACY_CONTACT` |
| 适用法域 | `PRIVACY_JURISDICTION` |
| 第三方服务、日志和备份期限 | 由运营方另行公开 |

只有填写这些字段，并不代表已经满足某部法律。运营方仍需根据实际部署和用户所在地进行法律审查。

### 2. 系统可能处理的数据

- 账号信息：用户名、邮箱、密码哈希、OAuth 标识、头像和账号时间；
- 研究内容：聊天、claim、论文来源、工具记录、上传文件、Research Job、Claim Audit、Evidence Pack 和 provenance；
- 用户自带的模型 API Key（BYOK）：新保存的密钥加密存储，不会在读取接口中返回明文。旧版升级数据库可能仍有历史明文 JSON 或 `anthropic_api_key` 字段；运营方必须审计和迁移，用户下次保存设置时会将可读取的旧值改写到加密字段并清除旧字段；
- 运行数据：任务状态、模型/Provider、耗时、错误类别、token 与成本信息；
- 可选产品分析：用户标识、会话标识、事件类型、静态页面标签，以及经过限制的计数、耗时或结果分类；
- 基础设施日志可能包含 IP、请求时间、状态码等。它们不属于下面的产品分析表，真实保留期由运营方公开。

公开论文、共享会话或公开评论会被其他人看到。请勿在公开内容中放入密钥或未授权的研究数据。

### 3. 可选产品分析

产品分析采用**明确同意**：

- 默认不同意；匿名用户和未同意的登录用户不会写入产品分析事件；
- 用户可以在隐私设置中同意或拒绝；
- 拒绝后，当前账号已有的产品分析事件会从在线数据库删除，尚未写入的缓冲事件也会丢弃；
- P1 生产目标为保留不超过 **30 天**。Celery Beat 每日清理过期事件；如果调度服务未运行，这个期限不会自动得到保证，因此运营监控必须检查清理任务；
- `PRODUCT_ANALYTICS_RETENTION_DAYS` 的真实生产值必须与线上说明一致。

下列内容**不得进入产品分析**：

- claim 原文、prompt 或聊天正文；
- 论文标题、URL、DOI、arXiv/Bibcode 等来源标识；
- 工具参数、工具结果或 Evidence Pack 内容；
- 错误原文、异常堆栈；
- 后验、参数、显著性、张力等科研数值。

系统只允许经过白名单和过滤的粗粒度产品事件。研究内容仍可能为了完成用户请求而发送给用户选择的模型或数据服务；这与产品分析是两件事。

本仓库不会自动把产品分析或研究内容导出到模型训练流程。任何额外用途都必须由具体运营方另行说明并取得所需授权。

### 4. 研究记录保留

研究记录和 Evidence Pack 没有 30 天自动删除期限。默认保留到用户主动删除相关记录、删除账号，或运营方公布的更短期限到达。其目的包括恢复研究任务、验证证据和检查科学 provenance。

请求外部模型、Google 登录、论文/天文档案、对象存储、托管或日志服务时，完成该请求所需的数据会离开本部署。运营方必须列出实际启用的第三方服务；第三方保留和删除规则由其自身条款决定。

### 5. 删除账号

`DELETE /api/auth/account` 需要用户名确认，并使用当前密码或已绑定的 Google 身份重新验证。成功接受请求后：

1. 系统先在数据库恢复边界之外写入带签名的删除 tombstone；写入失败时拒绝受理，而不是冒险继续；
2. 账号立即进入 `DELETION_PENDING`，登录凭据和保存的 API Key 被撤销，排队中或运行中的 Research Job 记录被标记为取消；已经发出的外部请求不一定能瞬间停止；
3. 后台任务异步删除与账号关联的在线数据库记录和对象存储文件；失败会标为可重试并由定时任务继续处理；
4. 用户会得到一次性删除回执。回执应保存到清理完成。

删除不是“所有副本瞬间消失”。P1 的备份保留目标是 **30 天**，删除回执也给出相应的 `backup_expiry`；但是数据库快照、对象版本和平台日志的真实到期必须由运营方在云平台配置并验证，本仓库不能替云服务删除备份。

外部 tombstone 只保存经过 HMAC 处理的用户指纹、回执哈希、请求时间和签名，不保存用户名、邮箱或研究内容。它用于阻止旧备份恢复已删除账号。代码不会自动删除外部 tombstone；运营方应至少保留到所有可能恢复该账号的备份都已过期，并公开更长保留的理由和期限。

账号删除只覆盖本部署控制的数据库和对象存储。已经发送给外部 Provider 的数据，需要按照该 Provider 的流程处理。

### 6. 浏览器和安全

浏览器会保存登录和界面状态。本地聊天草稿、聊天历史和操作日志可能包含研究文字；退出登录只移除认证状态，不保证清除这些记录。共享设备交接前应清除站点数据。

密码以哈希保存，BYOK 使用稳定的 Fernet Key 加密，证据使用独立签名密钥。没有系统能保证绝对安全。生产环境必须使用 TLS、受控密钥管理、访问受限且经过恢复演练的备份，以及独立的证据密钥轮换记录。

隐私请求请联系该实例公开的运营方。不要在 GitHub Issue 中提交个人数据、密钥或未公开研究内容。

---

## English

### 1. Operator information required in production

Every hosted instance must replace placeholders with real information:

| Item | Production setting |
|---|---|
| Operator/controller name | `PRIVACY_OPERATOR_NAME` |
| Privacy contact | `PRIVACY_CONTACT` |
| Applicable jurisdiction | `PRIVACY_JURISDICTION` |
| Subprocessors, log and backup retention | Published separately by the operator |

Populating these fields does not itself establish legal compliance. The
operator remains responsible for reviewing the actual deployment and user
locations.

### 2. Data the service may process

- account data, including username, email, password hash, OAuth identifier,
  avatar, and account timestamps;
- research content, including chats, claims, paper sources, tool records,
  uploads, Research Jobs, Claim Audits, Evidence Packs, and provenance;
- user-provided model API keys (BYOK). Newly saved keys are encrypted and are
  not returned in plaintext by the read API. An upgraded database may still
  contain legacy plaintext JSON or the old `anthropic_api_key` field;
  operators must audit and migrate it. The next settings save rewrites any
  readable legacy value into the encrypted field and clears the old field;
- operational data such as task state, model/provider, latency, error class,
  token use, and estimated cost;
- optional product analytics: user/session identifiers, event type, static
  page labels, and bounded count, duration, or outcome buckets;
- infrastructure logs may contain IP address, request time, and status code.
  Those logs are separate from the product-analytics table and need an
  operator-published retention period.

Public papers, shared sessions, and public comments can be seen by other
people. Do not place secrets or unauthorized research data in public content.

### 3. Optional product analytics

Product analytics require **explicit opt-in consent**:

- anonymous users and signed-in users who have not opted in are not recorded;
- users can opt in or out in Privacy Settings;
- opting out deletes that account's existing online analytics events and
  discards buffered events;
- the P1 production target is a maximum **30-day** retention period. Celery
  Beat schedules a daily purge. Operators must monitor that job because the
  repository cannot guarantee the deadline while the scheduler is down;
- the deployed `PRODUCT_ANALYTICS_RETENTION_DAYS` value must match the notice
  shown to users.

Product analytics must never contain:

- claim text, prompts, or chat content;
- paper titles, URLs, DOI, arXiv identifiers, or Bibcodes;
- tool arguments, tool results, or Evidence Pack content;
- raw error messages or exception traces;
- scientific values such as posterior parameters, significance, or tension.

Only allowlisted, scrubbed, coarse product events are accepted. Research
content may still be sent to the model or archive service selected to perform
the user's request; that processing is separate from product analytics.

This repository does not automatically export analytics or research content
to a model-training pipeline. Any additional use must be disclosed and
authorized by the operator of the specific instance.

### 4. Research-record retention

Research records and Evidence Packs do not use the 30-day analytics TTL. They
are retained until the user deletes the relevant records or account, or until
a shorter operator-published period applies. This supports workflow recovery,
evidence verification, and scientific-provenance review.

Requests to external model providers, Google sign-in, literature/astronomy
archives, object storage, hosting, or logging services send the data needed to
perform that request outside this deployment. Operators must list the services
they actually enable. Each third party has its own retention and deletion
terms.

### 5. Account deletion

`DELETE /api/auth/account` requires exact username confirmation and
reauthentication with the current password or linked Google identity. Once the
request is safely accepted:

1. a signed deletion tombstone is written outside the database restore
   boundary first; the request fails closed if that write fails;
2. the account immediately becomes `DELETION_PENDING`, reusable credentials
   and saved API keys are revoked, and queued/running Research Job records are
   marked cancelled. An already-issued external request may not stop instantly;
3. a background task erases connected online database rows and object-storage
   files. Failures are retryable and periodic reconciliation schedules them
   again;
4. the user receives a one-time deletion receipt and should retain it until
   cleanup completes.

Deletion does not mean every copy disappears instantly. P1 targets a **30-day**
backup-retention window and returns a corresponding `backup_expiry`, but the
operator must configure and verify expiry of database snapshots, object
versions, and platform logs. This repository cannot delete a cloud provider's
backup by declaration alone.

The external tombstone contains only an HMAC-derived user fingerprint, receipt
hash, request time, and signature—not username, email, or research content. It
prevents an older backup from reactivating a deleted account. The code does not
automatically purge external tombstones; an operator must retain each one at
least until every backup capable of restoring that account has expired and
must disclose any longer retention.

Account erasure covers the database and object storage controlled by this
deployment. Data already sent to an external provider is governed by that
provider's deletion process.

### 6. Browser data and security

The browser stores authentication and interface state. Local chat drafts,
chat history, and the operation log may contain research text. Signing out
removes authentication state but does not guarantee that those records are
cleared; clear site data before handing a shared device to another person.

Passwords are hashed, BYOK secrets use a stable Fernet key, and evidence uses
an independent signing key. No service can promise absolute security.
Production needs TLS, controlled key management, access-restricted and
restore-tested backups, and documented evidence-key rotation.

Send privacy requests to the operator contact published by the hosted
instance. Do not put personal data, secrets, or unpublished research in a
GitHub issue.

## Operator publication checklist / 运营方发布检查

- Fill all three `PRIVACY_*` settings with real values.
- Publish enabled third parties, infrastructure-log retention, and the real
  backup/versioning schedule.
- Keep `CLAIM_AUDIT_ENABLED=false` until privacy, deletion, evidence, and Daily
  release gates have passed.
- Verify the daily analytics purge and the account-deletion reconciliation job.
- Test deletion against a restored backup and confirm the external tombstone
  prevents account resurrection.
- Update this notice whenever implementation or deployment behavior changes.
