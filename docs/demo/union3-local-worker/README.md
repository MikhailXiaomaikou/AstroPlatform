# Union3 local-worker research-loop demo / Union3 本地算力科研闭环演示

This recorder exercises the real guarded path that is currently available on
the development Mac:

```text
private Workspace
→ checksum-pinned Union3 arXiv v4 Reader
→ queued signed HTTPS task
→ real local profile-χ² calculation
→ independent verifier checks the immutable task binding
→ missing signed OCI image digest is rejected
→ WITHHELD; no review, SUPPORTED, or Evidence Pack
```

录制器执行的是真实受控路径，不会向数据库塞入预制的 `SUPPORTED`，也不会伪造 OCI digest。当前机器没有 Docker/Podman，不能诚实证明计算是在 GitHub Actions 构建并由 Cosign 签名的 Worker 镜像中完成，因此本视频的**预期结论就是 `WITHHELD`**。待真实镜像构建、签名、按 digest 运行后，才可以继续独立复算、不同账号人工审核、`SUPPORTED` 和 Evidence Pack。

The expected outcome of this recording is deliberately **`WITHHELD`**, not
`SUPPORTED`. It demonstrates that a real numeric result cannot cross the
scientific gate when its execution environment is not bound to the signed OCI
Worker image.

## Honest boundary / 诚实边界

- The scientific result is a reproduction of one published Union3 Table 9 interval. It is not a new discovery and remains `publication_ready=false`.
- The recording runs the science-worker code as a host process so it can be reproduced on a development Mac without Docker. Therefore it does **not** claim that the production container-signing/hardening gate was tested.
- The local control-worker helper calls the exact production reconciliation and independent-verifier code, but it does **not** claim that Render itself was deployed or observed for 72 hours.
- Review, deterministic finalization, and Evidence Pack creation are not attempted after the machine gate fails. A human review is not allowed to override this refusal.
- The recorder starts a single-process RESP2 nonce fixture that implements atomic `SET NX EX`, so signed Worker requests retain replay protection without requiring Homebrew Redis. It rejects non-nonce queue commands and does **not** claim to test production Redis durability, clustering, or Celery transport.
- The recorder also starts a localhost-only HMAC capability upload fixture. It exercises direct HTTP PUT, server-side byte re-download, SHA-256 verification, and promotion into a Worker-unwritable authoritative key, but it does **not** claim to test production S3/R2, bucket versioning, or SigV4.
- The next required step is to merge and run `.github/workflows/worker-image.yml`, obtain its immutable multi-architecture digest and Cosign verification, and execute the Worker from that image on a Docker-capable machine.

## Requirements / 依赖

- the supported backend environment at `/Users/chenkexuan/Projects/astro-platform/backend/venv/bin/python`, or `--backend-python`;
- installed `frontend/node_modules` including Playwright;
- Google Chrome or Playwright Chromium;
- `pdftotext`, `ffmpeg`, Node.js, and npm;
- network access to the registered `https://arxiv.org/pdf/2311.12098v4` artifact;
- free local ports 8010, 5180, 6399, and 6400 (overridable with
  `--backend-port`, `--frontend-port`, `--redis-port`, and `--artifact-port`).

Docker and an installed Redis server are not required for this laptop recorder. The production Worker image and Render/Redis/Celery deployment have separate release gates.

## Record / 录制

From the repository root:

```bash
/Users/chenkexuan/Projects/astro-platform/backend/venv/bin/python \
  docs/demo/union3-local-worker/run_local_demo.py \
  --output-dir /private/tmp/standard-astro-union3-demo
```

The runner refuses a dirty Git tree by default because the receipt binds the run to a commit. During implementation only, `--allow-dirty` permits a trial and marks `git_dirty=true`; do not publish that trial as a provenance demo.

The first arXiv download can be slow. A previously downloaded exact v4 source
tar and PDF may be reused without weakening the Reader:

```bash
--source-cache-dir /private/tmp/union3-demo-source-cache
```

The directory must contain `2311.12098v4.tar.gz` and `2311.12098v4.pdf`.
The runner compares both files to the registry SHA-256 values before putting
them in the local object cache; the Reader then checks them again, validates
the tar structure, runs `pdftotext`, and records the acquisition chain. The
receipt says whether this verified cache was preloaded.

Outputs:

```text
standard-astro-union3-local-worker-demo.mp4
union3-local-worker-demo-poster.png
union3-demo-receipt.json
union3-primary-analysis.json
```

The receipt records the actual source, extraction, claim, primary calculation,
Audit refusal, scientific boundary, and missing-OCI gate. It explicitly records
that no Evidence Pack exists. Ephemeral credentials and the local ledger are
removed after the run. Use `--keep-state` only for debugging; the retained
directory contains private development credentials.

## Recorded artifact / 已录制产物

The checked-in recording was produced from clean commit
`4c9de6e27b19d7111c8307ce25ccd617f22399af` on 2026-07-20. Its expected and
observed verdict is `WITHHELD`.

- [MP4 video](artifacts/standard-astro-union3-local-worker-demo.mp4)
- [Poster](artifacts/union3-local-worker-demo-poster.png)
- [Machine-readable receipt](artifacts/union3-demo-receipt.json)
- [Primary numerical analysis](artifacts/union3-primary-analysis.json)
- [SHA-256 manifest](artifacts/SHA256SUMS)

仓库内的正式录制来自干净提交
`4c9de6e27b19d7111c8307ce25ccd617f22399af`。它证明数值主计算和安全拒绝都真实
发生过，但不代表生产 S3、Render、签名 OCI、人工审核或 `SUPPORTED` 已完成。

## Failure diagnosis / 失败排查

Use `--state-dir /private/tmp/astro-union3-debug --keep-state` to retain:

```text
backend.log
frontend.log
control-worker.log
nonce-redis.log
artifact-store.log
astro-demo.db
worker-home/
objects/
```

Do not copy the state directory into the repository. This recording is a
guardrail demo, not the completed `SUPPORTED` research-loop demo. Never relabel
its real `WITHHELD` result as a successful closed loop.
