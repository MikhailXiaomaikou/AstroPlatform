# Workflow Foundry：AI 候选代码落盘与合并操作手册

这条流程只解决一个问题：把已经跑过 Demo、并经过工作流审核的 AI 候选补丁，安全地变成 GitHub `main` 中的真实代码。它不会直接把候选变成正式科研工具。

## 安全边界

流程分成两次独立动作：

```text
已审核的候选版本
→ 受保护工作流重放原始补丁
→ 创建 Draft PR（绝不自动合并）
→ 人工检查并合并
→ 另一个受保护工作流核对 PR 和 merge commit
→ 从 merge commit 构建全新的非正式 Validation 镜像
→ 创建新的 CandidateVersion
→ 重新跑 Demo、重新做工程/科学审核
```

旧版本的 Demo 和审核不会转移到新版本。新版本初始状态为 `BUILDING`。即使落盘成功，如果代码没有实现镜像中已经审核的静态 execution adapter 和独立 verifier，Formal Registry 仍会拒绝注册。

## Render 配置（默认全部关闭）

```text
FOUNDRY_SOURCE_MATERIALIZATION_ENABLED=false
FOUNDRY_MATERIALIZATION_DISPATCH_BACKEND=disabled
FOUNDRY_MATERIALIZATION_RESULT_SECRET=<独立随机值，至少 32 字符>
FOUNDRY_MATERIALIZATION_ATTESTATION_VERIFICATION_KEYS={"materialization-2026-01":"<base64 Ed25519 公钥>"}
FOUNDRY_MATERIALIZATION_GITHUB_TOKEN=<只允许 workflow dispatch 的细粒度 token>
FOUNDRY_MATERIALIZATION_GITHUB_REPOSITORY=<owner/repo>
FOUNDRY_MATERIALIZATION_GITHUB_WORKFLOW=foundry-materialize-candidate.yml
FOUNDRY_MATERIALIZATION_FINALIZE_GITHUB_WORKFLOW=foundry-finalize-materialization.yml
FOUNDRY_MATERIALIZATION_GITHUB_REF=main
```

Render 只保存公钥和 callback bearer，绝不能保存 materialization 私钥。

## GitHub 配置

建立四个互相隔离的 GitHub Environments：

| Environment | 权限与秘密 | Required reviewers |
|---|---|---|
| `foundry-materialization-pr` | 无长期秘密；保护 `contents:write` 和 `pull-requests:write` 临时令牌 | 不要求；流程只创建 Draft PR，不会合并 |
| `foundry-materialization-build` | 无长期秘密；保护 `packages:write` 临时令牌 | 不要求；流程只构建非正式镜像 |
| `foundry-materialization-attestation` | 只保存 materialization Ed25519 私钥与 key id | 至少 1 名人工批准者 |
| `foundry-materialization-callback` | 只保存 callback bearer 和控制面 URL | 不要求；只上传已经签名的冻结回执 |

四个 Environment 都必须把 `Deployment branches and tags` 设置为
`Selected branches and tags`，且只允许 `main`。这是开启物化功能前的硬性条件，
不是建议。每个有写权限或秘密的 Job 还会检查
`GITHUB_REF=refs/heads/main`，但 shell 检查不能替代 GitHub 侧的部署分支策略。

`foundry-materialization-attestation` 中配置：

```text
Secret: FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY=<base64 32-byte Ed25519 seed>
Variable: FOUNDRY_MATERIALIZATION_ATTESTATION_KEY_ID=materialization-2026-01
```

`foundry-materialization-callback` 中配置：

```text
Environment secret: FOUNDRY_MATERIALIZATION_RESULT_SECRET=<与 Render 相同>
Environment variable: FOUNDRY_CONTROL_PLANE_URL=https://<backend-domain>
```

私钥必须与 Registry、Formal Build、Worker 和 Evidence Pack 的私钥不同。
Attestation Environment 不能保存 callback bearer；Callback Environment 也不能保存
attestation 私钥。PR/Build Environment 两者都不能保存这两类秘密。

## 操作顺序

1. 审核者在 Candidate 精确版本上确认 PASSED Demo 和所需工作流审核。
2. 调用 `POST /api/admin/foundry/candidates/{candidate_id}/materialize`，请求体只含 `candidate_version_id` 和 `candidate_version_hash`。
3. 系统从不可修改的 Draft 收据重建 artifact id、run id、digest、base commit、patch hash 和目标模块路径。
4. GitHub 工作流只对该单一模块执行 `git add -- <server-bound-path>`，检查 staged diff，建立确定性分支和 Draft PR。它不会执行候选代码，也没有 callback 或签名私钥。
5. 独立签名 job 对 PR 收据做 Ed25519 签名；Render 同时验证 bearer 与签名。
6. 人工在 GitHub 审查并合并 PR。任何自动合并都不在本流程中。
7. 调用 `POST /api/admin/foundry/candidates/{candidate_id}/materialization-finalize`，只提交 `materialization_attestation_id`。
8. 第二个工作流核对 PR 已合并、`baseRefName=main`、PR head 仓库就是当前仓库、
   head SHA 和 merge SHA 都准确，并用完整 Git 历史证明 merge commit 是
   `origin/main` 的祖先。随后再核对原始 artifact、patch 和模块字节，从精确 merge
   commit 构建新 Candidate Validation 镜像，但不运行镜像。这些 main/repository/
   ancestry 字段进入 Ed25519 签名回执，控制中心收到后还会逐项复核。
9. 签名 final receipt 后，控制中心追加不可修改收据，并创建新的 CandidateVersion。
10. 对新版本重新执行 Validation Demo 和全部工作流审核。只有之后才能申请 Formal Build；Formal Registry 的静态 adapter 检查仍然生效。

## 故障处理

- `materialization_dispatch_disabled`：功能开关或 GitHub dispatch 尚未启用；源代码没有变化。
- `materialization_branch_conflict`：确定性分支已存在但 commit 不一致；停止并人工检查，不强推。
- `materialization_*_binding_mismatch`：artifact、PR、hash 或版本不一致；停止，不接受 callback。
- `materialization_module_bytes_changed`：merge 后模块不等于原审核补丁；停止，不创建新版本。
- `materialization_origin_no_longer_current`：等待期间 Candidate 已产生新版本；旧审核不能复用。
- `formal_materialization_receipt_mismatch`：Formal Build 与 merge receipt 不一致；禁止进入 Registry。

所有功能在 dark deployment 时保持关闭。只有 GitHub Environment、公开验签 keyring、回调密钥、迁移和真实端到端演练全部通过后，才把 `FOUNDRY_SOURCE_MATERIALIZATION_ENABLED` 与 dispatch backend 打开。
