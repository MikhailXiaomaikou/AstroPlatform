# Foundry AI Draft Provider Contract / AI 草稿提供方合同

AI Draft Job 只负责写“候选设计稿”，不运行它。候选计算与 Demo 由另一个、没有 AI 密钥的 Validation workflow 完成。

The AI Draft Job writes an inert candidate proposal only. It never runs the candidate. A separate Validation workflow, without an AI credential, runs any Demo later.

## 1. GitHub 配置 / GitHub configuration

控制中心需要以下变量：

```text
FOUNDRY_DRAFT_DISPATCH_BACKEND=github_actions
FOUNDRY_DRAFT_GITHUB_REPOSITORY=owner/repository
FOUNDRY_DRAFT_GITHUB_WORKFLOW=foundry-candidate-draft.yml
FOUNDRY_DRAFT_GITHUB_REF=main
FOUNDRY_DRAFT_GITHUB_TOKEN=<fine-grained Actions:write token>
FOUNDRY_DRAFT_RESULT_SECRET=<independent random secret, at least 32 characters>
```

GitHub Actions 还需要：

```text
Variable: FOUNDRY_AI_DRAFT_PROVIDER_COMMAND_JSON
Variable: FOUNDRY_CONTROL_PLANE_URL
Secret:   FOUNDRY_AI_DRAFT_API_KEY
Secret:   FOUNDRY_DRAFT_RESULT_SECRET
```

`FOUNDRY_AI_DRAFT_PROVIDER_COMMAND_JSON` 是一个 JSON 字符串数组，例如：

```json
["/opt/standard-astro/foundry-provider-adapter", "draft"]
```

系统使用 `shell=False` 启动这个受信任的适配器。没有配置命令、命令失败、超时或输出格式错误时，Draft 明确记录为 `FAILED`；系统不会补造一个成功结果。

The adapter is invoked with `shell=False`. Missing configuration, non-zero exit, timeout, or malformed output is recorded as `FAILED`; no success is synthesized.

## 2. 适配器输入 / Adapter input

适配器从标准输入读取 JSON。它只会收到：

- `draft_run_id` 和 `candidate_id`；
- `gap_fingerprint` 和非敏感的 `gap_code`；
- 控制中心白名单化并用 canonical JSON 固定的 `gap_descriptor`，只可包含
  `dataset_key(s)`、`workflow_key`、`source_profile_key`、`claim_schema/type`、
  `model`、`parameter`、`statistic`、`evidence_kind`、`supported_selection`
  与固定的 `research_domain=cosmology`；
- `generation_route` 与 `risk_level`；
- 固定的非正式候选约束。

它不会收到用户、Workspace、claim、用户 prompt、论文正文或来源文本。

The adapter receives no user identity, workspace, claim, source document, or user prompt.

## 3. 适配器输出 / Adapter output

标准输出必须只有一个 JSON 对象：

```json
{
  "schema_version": 1,
  "candidate_bundle": {
    "candidate_version": 0,
    "generation": {
      "model": "provider/model",
      "prompt_or_claim_stored": false,
      "generated_code_executed_by_draft_job": false
    }
  },
  "patch": "an inert unified diff for one allowlisted generated module",
  "sbom": {"proposed_components": []},
  "provider": {
    "provider": "provider-name",
    "model": "model-name",
    "request_id": "optional-provider-request-id"
  }
}
```

`candidate_bundle` 仍须满足项目的 Candidate Bundle Schema。`candidate_version` 必须省略、为 `null` 或为 `0`，因为真实版本号由控制中心分配。

The candidate bundle must still satisfy the repository Candidate Bundle Schema. The real immutable version number is assigned by the control plane.

AI job 本身不应用、导入、测试或执行 patch。没有 AI 密钥的宿主 job 会在一次性
checkout 中用 `git apply --check --index` 应用允许列表内的 patch，仅用于计算精确的
源码 tree hash、构建并固定候选 Validation 镜像。候选代码第一次执行只发生在另一个
无网络、无 AI 密钥的隔离 Validation 容器中。

The AI job does not apply, import, test, or execute the patch. A host job without
the AI credential applies the allowlisted patch in a disposable checkout only to
derive the exact source-tree hash and build a digest-pinned Candidate Validation
image. Candidate code first executes later in a separate, network-disabled
Validation container with no AI credential.

## 4. 密钥边界 / Credential boundary

```text
draft-with-ai job
  protected environment: foundry-candidate-draft-ai（只允许 main）
  sees: AI provider key
  cannot see: Draft callback, DB, Registry, Evidence or Worker keys

materialize-and-build-without-callback job
  protected environment: foundry-candidate-draft-build（只允许 main）
  sees: no AI key and no Draft callback secret
  can: apply allowlisted inert patch, hash source, build Candidate image

callback-only job
  protected environment: foundry-candidate-draft-callback（只允许 main）
  sees: Draft callback secret
  cannot: check out candidate source, build an image, or execute candidate code
```

三个 Environment 的 `Deployment branches and tags` 都必须设为只允许 `main`。
这是启用 AI Draft 前的硬性安全条件，不是可选建议。控制面也只会用
`workflow_dispatch ref=main`；workflow 内的 `GITHUB_REF` 检查用于故障闭合，但不能
替代 GitHub 侧的 Environment 分支限制。

Host materialization checks and hashes the artifact after the AI process has exited, then uploads one frozen callback document. A separate callback-only job posts that document. The resulting version has `created_by_kind=AI_DRAFT_JOB`, remains non-formal, and cannot be reviewed or registered by the AI identity itself.

宿主物化 Job 会在 AI 进程退出后重新检查并计算哈希，再上传一份冻结的回调文档。另一个仅回调 Job 负责提交该文档。生成的版本标记为 `AI_DRAFT_JOB`，仍是非正式候选；AI 身份本身没有审核或注册权限。
