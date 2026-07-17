# Claim Audit demo / 主张审计演示

[![Standard Astro Claim Audit demo poster](./poster.png)](./standard-astro-claim-audit-demo.mp4)

点击封面播放 32 秒 MP4。这是用一次真实本地运行的界面截图制作的分镜演示，不是连续录屏，也没有伪造“成功”结果。<br>
Click the poster to play the 32-second MP4. It is a storyboard made from UI captures of one real local run, not a continuous screen recording or a fabricated success result.

## 案例 / Case

- Claim / 主张：`DESI DR2 BAO, CMB, and Pantheon+, Union3, and DES-SN5YR prove that dark energy evolves with time.`
- Source / 来源：DESI 官方 DR2 cosmology chains 与 data products 发布页面。
- Mode / 模式：`execute_registered`
- Registry key / 注册键：`desi_dr2_bao`

## 实际结果 / Observed result

| 层面 / Layer | 结果 / Result | 含义 / Meaning |
|---|---|---|
| Runtime / 运行 | `COMPLETED` | 任务流程完成，没有把 provider 或系统错误冒充科学判断。 |
| Science / 科学 | `CAPABILITY_GAP` | 当前运行缺少经 checksum 验证的完整 DESI DR2 official chain mirror。 |
| Gap / 缺口 | `registered_data_unavailable` | 下一步是安装并核验官方 chain mirror 后重试。 |
| Evidence Pack / 证据包 | `Finalized` + verified | 服务端签名与文件内容均通过验证。 |

This demonstrates the key product rule: a technically completed job is not automatically a supported scientific claim. Missing evidence fails closed, while the unresolved run still receives a signed, reviewable Evidence Pack.

这段演示只说明“当前运行证据不足”，不代表否定 DESI 数据，也不判断动态暗能量是否真实存在。完整事实记录见 [product-facts.md](./product-facts.md)。

## 文件 / Files

- [MP4 demo / 演示视频](./standard-astro-claim-audit-demo.mp4) — 1920×1080, 30 fps, H.264 + AAC, 32 seconds.
- [Poster / 封面](./poster.png)
- [Storyboard / 分镜源文件](./storyboard.html)
- [Build script / 构建脚本](./build-demo.sh)
- [SHA-256 checksums / 文件校验](./SHA256SUMS)
- `assets/` — interface captures from the real run / 真实运行界面截图。
- `frames/` — bilingual rendered storyboard frames / 中英双语分镜帧。

## 重新生成 / Rebuild

需要 `ffmpeg`。从本目录运行：

```bash
bash build-demo.sh
```

脚本会覆盖并重建 MP4；若要修改字幕或版式，先编辑 `storyboard.html` 并重新捕获 `frames/*.png`。画面中的 `Created by Huashu-Design` 是分镜制作流程署名，不是 Standard Astro 产品界面的一部分。

Requires `ffmpeg`. The script overwrites and rebuilds the MP4 from the checked-in frames. Edit `storyboard.html` and recapture the frames first when changing the layout or copy. The visible `Created by Huashu-Design` mark credits the storyboard production workflow; it is not part of the Standard Astro product UI.
