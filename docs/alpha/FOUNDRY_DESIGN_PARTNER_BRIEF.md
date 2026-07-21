# Workflow Foundry design-partner Alpha / 工作流工厂设计伙伴 Alpha

> **Status / 状态:** recruiting design partners for scheduled private tests;
> the hosted production service is not open for self-service use yet.
> **当前状态：**正在招募预约式私测伙伴；托管生产服务尚未开放自助使用。

## The 20–30 minute test / 20–30 分钟测试内容

Standard Astro is an open-source cosmology workbench that separates AI
exploration from formal scientific evidence. In this Alpha, one narrowly
defined capability gap becomes a recorded workflow candidate:

```text
CAPABILITY_GAP
→ AI candidate draft
→ isolated Demo
→ immutable Demo record
→ human review boundary
```

Standard Astro 是一个开源宇宙学工作台，把 AI 探索和正式科学证据分开。本次
Alpha 会把一个范围明确的能力缺口转成候选工作流：AI 可以生成候选并运行隔离
Demo，但不能批准自己，也不能把 Demo 写成正式科研结论。

The first test uses public DESI DR2 chain metadata or the public Union3
distance product. A participant will:

1. inspect one fixed scientific contract;
2. run or watch one candidate workflow on a local machine;
3. check its source pins, checksums, limitations, and failure state; and
4. tell us where the contract, provenance, or interface could mislead a real
   researcher.

第一个测试使用公开的 DESI DR2 chain 元数据或 Union3 距离数据。参与者只需检查
一个固定科学合同、运行或观看一个候选流程，并指出科学语义、来源记录或界面中
可能误导真实研究者的地方。

## What this Alpha does not claim / 本次 Alpha 不声称什么

- A candidate Demo is always `NON_FORMAL_DEMO`.
- It cannot output `SUPPORTED`, a publication-ready result, or a new discovery.
- A formal result would additionally require a registered workflow, an
  independent implementation, signed provenance, and human scientific review.
- Participation is not an endorsement. We do not publish a participant's name
  or institution without an explicit opt-in.

- 候选 Demo 永远是 `NON_FORMAL_DEMO`（非正式演示）。
- 它不能输出 `SUPPORTED`、可发表结果或新发现。
- 正式结果还需要已注册工作流、独立实现、签名来源记录和人工科学审核。
- 参与测试不等于为项目背书；未经明确同意，我们不会公开参与者姓名或机构。

## What you need / 需要准备什么

- 20–30 minutes;
- macOS or Linux, ideally with Docker, for the hands-on path;
- no private data, unpublished chain, institutional credential, or API key;
- critical feedback is more useful than praise.

- 20–30 分钟；
- 实操路径建议使用装有 Docker 的 macOS 或 Linux；
- 不需要私有数据、未公开 chain、机构账号或 API Key；
- 批评和失败记录比表扬更有价值。

A guided screen-share or recorded walkthrough is available if local execution
is inconvenient. Windows Docker Desktop support is part of the test scope but
has not yet completed the same end-to-end verification.

The public [18-second Candidate Demo](../demo/foundry-candidate/README.md)
shows one real fail-closed run and includes sanitized receipts and SHA-256
checksums.

如果不方便本地运行，可以选择引导式屏幕共享或录像 walkthrough。Windows Docker
Desktop 属于测试范围，但尚未完成与 macOS/Linux 相同等级的端到端验证。
公开的 [18 秒候选演示](../demo/foundry-candidate/README.md)展示了一次真实的安全失败
运行，并附带脱敏收据和 SHA-256 校验值。

## Feedback we need / 最需要的反馈

- Is the scientific contract narrow and unambiguous?
- Are dataset versions, covariance assumptions, and interval semantics visible?
- Does a failure explain the missing evidence instead of guessing a result?
- Is the local Worker installation acceptable for a research laptop?
- What would stop you from using this for a real reproducibility task?

- 科学合同是否足够窄、没有歧义？
- 数据版本、协方差假设和区间含义是否清楚？
- 失败时是否说明缺失证据，而不是猜出结果？
- 本地 Worker 的安装方式是否适合科研电脑？
- 什么问题会阻止你把它用于真实复现任务？

## Safety, privacy, and contact / 安全、隐私与联系

Please read the [privacy notice](../../PRIVACY.md),
[security policy](../../SECURITY.md), and the
[current limitations](../../README.md#当前状态--current-status). Test records
remain private by default and can be deleted on request.

请先阅读[隐私说明](../../PRIVACY.md)、[安全策略](../../SECURITY.md)和
[当前限制](../../README.md#当前状态--current-status)。测试记录默认私有，并可
申请删除。

To volunteer, open a
[Quick Feedback issue](https://github.com/MikhailXiaomaikou/Standard-Astro/issues/new?template=quick_feedback.yml)
with the title `Design-partner Alpha`, or contact the project through the
public channel that linked you here. Please do not post private datasets,
credentials, unpublished results, or embargoed collaboration material.

如愿意参加，可创建标题为 `Design-partner Alpha` 的
[Quick Feedback issue](https://github.com/MikhailXiaomaikou/Standard-Astro/issues/new?template=quick_feedback.yml)，
或通过邀请您看到本页的公开渠道联系项目。请勿提交私有数据、密钥、未发表结果或
合作组保密材料。
