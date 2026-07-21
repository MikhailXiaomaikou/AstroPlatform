# Standard Astro Foundry Candidate Demo / 候选 Demo

This kit records a real, disposable local run of the Foundry candidate path at
Git commit `f9b637b1f7cf31e3ecad150abe50a7b0b674019f`.

本套件记录了 Foundry 候选路径在 Git 提交
`f9b637b1f7cf31e3ecad150abe50a7b0b674019f` 上的一次真实、隔离的本地运行。

## What happened / 实际发生了什么

```text
CAPABILITY_GAP
→ capability request
→ candidate + immutable version
→ validation dispatched
→ real candidate Demo
→ DEMO_RECORDED
```

The candidate checks the repository-pinned DESI DR2 official-chain registry.
Registry integrity passed, but no byte-pinned official chain mirror was supplied,
so the scientifically correct result is:

候选工具检查仓库中固定版本的 DESI DR2 官方链注册信息。注册表完整性检查通过，
但运行环境没有提供逐字节固定的官方链镜像，因此正确结果是：

```text
status=PARTIAL
failure_class=official_chain_mirror_unavailable
evidence_class=NON_FORMAL_DEMO
publication_ready=false
claim_eligible=false
evidence_pack_allowed=false
```

No posterior interval was exposed. The Demo did not produce `SUPPORTED` and is
not formal scientific evidence. It was run entirely against a disposable local
SQLite database and local services; production was not used.

系统没有输出任何后验区间，也没有产生 `SUPPORTED`。这个 Demo 不是正式科研证据。
它只使用一次性的本地 SQLite 和本地服务，完全没有依赖生产环境。

## Files / 文件

- `standard-astro-foundry-local-demo.mp4`: 18 s, H.264, 1440×900 walkthrough assembled from real UI captures; it is not a continuous screen recording.
- `poster-foundry-candidate-zh.png`: screenshot used as the poster.
- `demo-report.sanitized.json`: complete non-formal DemoReport; contains no local path or secret.
- `ledger-summary.sanitized.json`: redacted lifecycle and hash-chain receipt.
- `candidate-bundle.json`: the exact declarative candidate and WorkflowSpec used by the historical run.
- `candidate-version-envelope.json`: the exact historical bootstrap identity envelope and its explicit provenance limits.
- `runner-descriptor.json`: the exact local descriptor whose SHA-256 produced the historical `runner_image_digest`.
- `ledger-events.json`: every canonical event envelope needed to recompute the append-only event chain.
- `run-candidate-demo.sh`: repository-relative replay command.
- `SHA256SUMS`: hashes for every published file except itself.

The video is a concise storyboard of the recorded run. The sanitized JSON
receipts and replay script, rather than the video edit, are the machine-readable
record of what executed.

视频是这次真实运行的简短分镜，不是连续录屏。可机器检查的执行记录以脱敏 JSON
收据和重跑脚本为准，而不是以视频剪辑为准。

## Verify the recorded receipt / 验证历史收据

```bash
./docs/demo/foundry-candidate/run-candidate-demo.sh --verify-recorded
```

This verifies exact file coverage, rejects symlinks, recomputes the DemoReport
and environment hashes, recomputes the complete CandidateVersion envelope,
recomputes the local runner-descriptor digest, and verifies every canonical
event envelope and previous-event link through `DEMO_RECORDED`.

该命令会检查所有公开文件是否都进入校验清单并拒绝符号链接，重新计算 DemoReport、
环境、完整 CandidateVersion envelope 和本地 Runner 描述符的哈希，并逐条验证直到
`DEMO_RECORDED` 的事件 envelope 与前序链接。

Passing this check proves that the published bootstrap receipt is internally
consistent. It does **not** prove complete historical environment closure: the
old `code_tree_sha256` covered one runner file, while its patch and SBOM hashes
were explicit bootstrap placeholders. The receipt is therefore non-formal and
cannot be upgraded after the fact.

These are Git-version-anchored consistency receipts, not digital signatures or
independent proof of authorship. Authenticity still depends on obtaining this
repository version from a trusted channel; formal releases require signed
artifacts and a signed Registry snapshot.

验证通过只证明这份历史 bootstrap 收据内部一致，并**不**证明历史运行环境完整闭包：
旧 `code_tree_sha256` 只覆盖一个 Runner 源文件，patch 和 SBOM 哈希也是明确的启动阶段
占位值。这份记录仍是非正式 Demo，不能事后补材料升级成正式证据。

这些文件只是由 Git 版本锚定的内部一致性收据，不是数字签名，也不能独立证明作者
身份。真实性仍取决于从可信渠道取得该仓库版本；正式发布必须使用签名产物和签名
Registry snapshot。

## Replay the current checkout / 重跑当前代码

From any clean Standard Astro checkout with backend dependencies installed:

在已安装后端依赖的 Standard Astro 仓库中运行：

```bash
./docs/demo/foundry-candidate/run-candidate-demo.sh \
  /tmp/standard-astro-foundry-replay
```

The script looks for `backend/venv`, then `backend/.venv`, and finally
`python3`. Use `PYTHON=/path/to/python` when dependencies are installed in a
different environment.

The checkout must be clean. The replay refuses tracked or untracked source
changes before binding `TOOL_VERSION` to the current commit, so a modified
runtime cannot be mislabeled as commit-pinned provenance.

The wrapper accepts only the documented, contract-valid outcomes: `PARTIAL`
when no official mirror was configured, or `PASSED` after a configured mirror
was fully verified. A configured but incomplete, corrupt, or mismatched mirror
is `FAILED` and exits non-zero. Dependency errors, runner exceptions, registry
failures, malformed summaries, and every other `FAILED` result also exit
non-zero.

A replay never reuses the historical `f4e8fa…` CandidateVersion. It calculates
a new identity from the current canonical tracked-source-tree hash, empty-patch
hash, dependency lock, installed-package inventory, runtime files, Python
binary, and local descriptor. The output `replay-identity.json` explicitly says
`historical_demo_version_reused=false`, `ledger_recorded=false`,
`environment_closure=DESCRIPTOR_ONLY`, and `formal_registry_eligible=false`.

脚本依次查找 `backend/venv`、`backend/.venv` 和 `python3`。如果依赖安装在
其他环境，请设置 `PYTHON=/path/to/python`。仓库还必须处于干净状态；脚本会在把
`TOOL_VERSION` 绑定到当前提交之前拒绝已跟踪或未跟踪的源码改动，避免把修改过的
运行环境误写成由某个提交固定。包装脚本只接受两种符合合同的成功结果：没有配置官方
镜像时为 `PARTIAL`，完整验证已配置镜像后为 `PASSED`。如果配置了镜像但文件缺失、损坏
或哈希不匹配，则结果为 `FAILED` 并以非零状态退出；依赖错误、Runner 异常、Registry
失败和摘要格式错误也同样失败。

当前重跑绝不会复用历史 `f4e8fa…` CandidateVersion。它会用当前规范化源码树、空 patch、
依赖锁、已安装包清单、运行文件、Python 二进制和本地描述符生成全新身份。输出的
`replay-identity.json` 会明确标记：未复用历史版本、未写入账本、只达到描述符级环境记录，
且不能进入正式 Registry。

If the script is outside the repository, provide the checkout path:

如果脚本不在仓库中，请指定仓库路径：

```bash
STANDARD_ASTRO_REPO=/path/to/astro-platform \
  PYTHON=/path/to/backend-python \
  ./run-candidate-demo.sh /tmp/standard-astro-foundry-replay
```

To verify a real official mirror, also set `DESI_DR2_OFFICIAL_CHAIN_ROOT` to a
local directory containing every pinned file. Missing or mismatched files fail
closed; the script never downloads unpinned data automatically.

如需验证真实官方镜像，把 `DESI_DR2_OFFICIAL_CHAIN_ROOT` 指向包含全部固定文件的本地目录。
缺文件或哈希不匹配都会安全失败；脚本不会自动下载未固定的数据。

## Trust boundary / 信任边界

The recorded `runner_image_digest` is a hash of a local runtime descriptor. It is
**not** a signed OCI image digest and cannot be promoted into the Formal Registry.
Formal registration still requires an isolated Validation Runner, signed image,
independent verifier, and human scientific review.

记录中的 `runner_image_digest` 只是本地运行描述符的哈希，**不是**已签名的 OCI
镜像摘要，不能用于正式注册。正式晋升仍需要隔离验证 Runner、签名镜像、独立复算
和人工科学审核。
