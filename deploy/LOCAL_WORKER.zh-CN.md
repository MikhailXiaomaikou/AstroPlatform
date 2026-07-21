# 签名本地科学 Worker / Signed local science worker

生产 Worker 不能直接执行 `docker compose up`。入口脚本会先确认镜像使用不可变 digest，再用 Cosign 验证它确实由 Standard Astro 官方 GitHub Actions 的版本标签签名，验证通过后才下载和启动。

The production Worker must be started through the preflight script. It verifies the immutable digest and the GitHub OIDC/Cosign identity before Docker pulls or runs the image.

准备官方发布的不可变镜像地址：

```bash
export ASTRO_WORKER_IMAGE='ghcr.io/mikhailxiaomaikou/standard-astro/science-worker@sha256:<release-digest>'
```

脚本会从已签名镜像内部读取构建时写入的 `TOOL_VERSION`，并把它作为
Worker 的代码身份。若另有官方 release manifest，可选设置
`GIT_COMMIT=<40-character-release-commit>` 做一致性检查；它不会被注入容器，
也不能覆盖镜像内的身份。

登记节点（一次性 code 不会写入配置文件）：

```bash
./deploy/start-signed-worker.sh enroll '<one-time-code>' \
  --control-plane 'https://your-control-center.example' \
  --name 'My science worker'
```

启动和查看状态：

```bash
./deploy/start-signed-worker.sh
./deploy/start-signed-worker.sh status
```

脚本只信任 `MikhailXiaomaikou/Standard-Astro` 仓库中的两个明确 OIDC 身份：`worker-image.yml` 的 `v*` 正式标签，以及受保护 `main` 上的 `foundry-formal-worker.yml`。其他分支或工作流、未签名镜像、浮动 tag、错误 commit、镜像内外 commit 不一致或错误 digest 都会在容器启动前被拒绝。
