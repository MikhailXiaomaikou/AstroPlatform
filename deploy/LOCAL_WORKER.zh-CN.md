# 签名本地科学 Worker / Signed local science worker

生产 Worker 不能直接执行 `docker compose up`。入口脚本会先确认镜像使用不可变 digest，再用 Cosign 验证它确实由 Standard Astro 官方 GitHub Actions 的版本标签签名，验证通过后才下载和启动。

The production Worker must be started through the preflight script. It verifies the immutable digest and the GitHub OIDC/Cosign identity before Docker pulls or runs the image.

准备三个值：

```bash
export ASTRO_WORKER_IMAGE='ghcr.io/mikhailxiaomaikou/standard-astro/science-worker@sha256:<release-digest>'
export WORKER_IMAGE_DIGEST='sha256:<release-digest>'
export GIT_COMMIT='<40-character-release-commit>'
```

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

脚本只信任 `MikhailXiaomaikou/Standard-Astro` 仓库中 `worker-image.yml` 在 `v*` 标签上的 OIDC 身份。分支构建、未签名镜像、浮动 tag、错误 commit 或错误 digest 都会在容器启动前被拒绝。
