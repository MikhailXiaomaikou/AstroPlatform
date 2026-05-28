# Cosmology M0 Capability Baseline

仓库: `astro-platform`
分支: `main`
时间窗: 2026-05-27 ~ 2026-05-28(一个工作会话)
状态: ✅ Shipped

---

## 1. Context

Cosmology 模块是 astro-platform 第一个 active vertical(2026 年初上线)。
本文档记录 2026-05-28 一轮**审查 + 修复 + 测试架构搭建 + 真实 LLM 行为验证**
之后,模块在四个维度上拿到的产线证据,作为后续 alpha 释放、模型升级、
新 vertical 接入时的回归参照。

本文档**不主张** cosmology 模块是"完成的"(始终有更多基准要加),只主张
**到本时间点为止,这四个维度的 capability 是被验证过的**。

---

## 2. 四维度产线证据

### 2.1 物理正确性

- DESI DR1 BAO 12 个均值 + 协方差对角逐项与 arXiv:2404.03002 published
  数值核对,误差 < 1e-3。
- CPL 暗能量闭式、`D_V = ∛(z·D_M²·D_H)`、Gauss-Legendre 积分、AP 比值
  误差传播——逐个推导确认。
- `distance_modulus_model` 与 `astropy.cosmology` 在 z ∈ {0.1, 0.5, 1.0, 2.3}、
  flat ΛCDM/wCDM/w0waCDM 三模型下,最坏偏差 **4×10⁻⁴ mag**(基准 1e-3)。
- `planck18` preset 现在按引用值 H0=67.36/Ωm=0.3153 算距离,不再悄悄走 astropy
  +BAO 内置(67.66/0.30966)——commit `f63bf0c` Fix 1。

**自动化回归**:`backend/scripts/benchmarks/run_cosmology_benchmarks.py`,
8 项基准,每次 push 到 main 由 `.github/workflows/ci.yml::benchmarks` job 自动跑,
JSON 上传 artifact。**当前 8/8 通过**。

### 2.2 反幻造架构(产线证据)

5 条不同攻击/误用路径在 2026-05-28 V4/V6 cosmology blind test 真实跑出来时
**都被对应防线拦下**:

| 攻击姿势 | 触发防线 | 真实 log 痕迹 |
|---|---|---|
| inline rows 强制 fit | `chain_tier=blocked` + `__do_not_claim__=True` | "Unsupported narrative gate BLOCKED reply" |
| 假 bibcode (`2099XYZ`) 引用 | grounded summary 替换 | "Unsupported cosmology anchor — replacing with grounded summary" |
| Galactic 对象(Helix Nebula)问 H0 | claim_validator zero-data hard-block | "Zero-data turn with 13 quantitative claims — hard-blocking" |
| 数据集越界外推(Pantheon+ at z=12) | 模型主动 abstain | trace 干净,reply 显式 abstain |
| `suspicious_author_year` 引用 | provenance violation block | "Citation provenance violation: kind=suspicious_author_year match='Kinkhabwala 1998'" |

**新增防线**:`_CITATION_KEYS_BLACKLIST`(commit `054e795`)堵 bibcode/doi
字符串里散落数字泄漏到数值池——红队 case
`numeric_in_bibcode_string_not_in_universe` 启用并通过。

### 2.3 CI 闭环

`0fb2d1b → b857baf` 这段时间 main 上的 push 都是 4 个 job 全绿:

| Job | 跑什么 | 时长 |
|---|---|---|
| `backend-test` | ~310 测试 + 45% cov gate | ~10-25 min |
| `frontend-test` | tsc + vitest 147 测试 + 4 mockE2E fixture | ~1-2 min |
| `benchmarks`(push-only) | cosmology + solar_system + exoplanet benchmarks + registry audit | ~1 min |
| `lint` | ruff app/ | ~10 sec |

`.github/workflows/daily.yml` 16:00 UTC cron + workflow_dispatch 手动触发,
带 `module` / `cases` / `provider` / `model` 4 个 input 支持细粒度 A/B。

### 2.4 真实 LLM 行为画像 + 路由修正

按时序:

| 轮次 | 模型 | Case 集 | ✓ / △ | 关键发现 |
|---|---|---|---|---|
| V6 | DeepSeek V4 Pro | 10 全 | 7 / 3 | 反幻造 5 防线全激活;A2/A3/E1 走 planner 路径而非专用工具 |
| V7 | (Opus 声称,实际 DeepSeek silent fallback) | A2/A3/E1 | △ / △ / △ | 发现 inference_router 的 `preferred_backend="anthropic"` → "claude" 没映射,silent fallback 到 DeepSeek |
| V10 | DeepSeek + 早起闸门 | A2/A3 | ✓ / ✓ | 闸门生效,A2 路由命中、A3 时间 160s → 22.9s |
| V11 | DeepSeek + 早起闸门 | 10 全 | **10/10** ✓ | 闸门精准命中:A2/A3 直接路径 ✓;D1/E1 仍走 `plan_research_program` 研究流程不误抓;B1/B2/C1/C2 反幻造防线齐发(zero-data/抵御伪 bibcode/galactic-object/越界外推)|

**V11 关键数据**(run id 26584788177,平均 102.6s/case):
- A2 `compare_luminosity_distances` 单工具命中,26.4s(对比 V6 多次走 planner 路径 100+s)
- A3 `assess_bao_bin_anomaly` 单工具命中,28.6s(对比 V6 160s)
- D1 8 工具组合(plan + matrix + assess_bao + compare_lum + 4×chain_run),正常研究 case 未被闸门吃掉
- E1 5 工具完整研究链(plan/matrix/evidence/verify/export)严格期望对齐

**模型层发现**:DeepSeek + Anthropic Opus 两个 OpenAI-style function-calling 模型
**都把"Hubble tension"/"Alcock-Paczynski"按 schema name 语义匹配优先送给
`plan_research_program`,无视 system prompt 内的路由指令**——这是模型层硬偏好
不是 prompt 问题,跑了 5 轮 prompt 工程(V1-V5)做了顶端表格、NEG-FIRST schema、
强制语气均无效。

**解决方案**:`app/api/chat.py::_cosmology_direct_route_from_prompt`(commit `6be3f8b` + `b857baf`),
仿照既有 `_inline_statistics_tool_call_from_prompt` 模式做服务端早起闸门——
触发关键词命中时**预跑专用工具,跳过 first LLM 调用**。每个 case 省 1 次 LLM 调用、
100% 命中路由。

---

## 3. 初步结论

到 2026-05-28 末:

> **Cosmology M0 模块在物理正确性、反幻造产线契约、CI 自动化闭环、工具路由确定性
> 这四件事上,都拿到了可复现的产线证据。**
>
> **可以放心说**(命题级):
> - 距离/距离模数计算的科学正确性,精度对齐 astropy 在 1e-3 mag 以内
> - 任何 LLM 现行行为下,5 类典型伪造路径会被对应防线拦下
> - 现有 CI 闭环对回归提供秒级反馈,push 到 main 即检测
> - DeepSeek 当前版本下,10/10 blind-test case 走严格期望路径
>
> **不主张**:
> - 模块"完成了"——更多 cosmology 工作流(强透镜、CMB lensing 全 likelihood、
>   非线性 S8 组合等)还在 phase-disabled
> - 反幻造架构能拦下**任何**伪造姿势——只主张 5 类已验证;新姿势出现时
>   红队语料库扩展
> - 自动化覆盖所有运行路径——Render 部署后健康、真浏览器 UI、真档案
>   连通性、长期模型漂移这些仍在产线测试外
> - LLM 行为长期稳定——本轮观察是单 cron 窗口快照,需要 7-30 天 daily 数据
>   累积才能说"行为稳定"

---

## 4. 当前已知边界(供下一轮投资参考)

| 维度 | 当前覆盖 | 未覆盖 |
|---|---|---|
| 物理 kernel | flat ΛCDM/wCDM/w0waCDM,massless ν | 曲率 Ωk、有质量中微子、辐射、Cobaya 真跑 |
| 反幻造防线 | 5 类(数值幻造/假引用/zero-data/越界外推/可疑作者-年份) | 语义级幻造(对的数字配错论文之类) |
| 多探针组合 | 线性 precision-matrix(compressed-Gaussian preliminary) | S8 非线性组合(σ8/Ωm → 派生 S8)、Pantheon+ 全 1701-SN 协方差 |
| LLM provider | DeepSeek V4 Pro 真实数据;Opus/Sonnet 未真激活 | Claude 真实行为需修 `preferred_backend` mapping(已修但需 token 验证) |
| 部署健康 | 无 smoke | Render 部署后状态、API 端点活性 |
| 真浏览器 UI | mock E2E 1 fixture | Playwright 配置就绪未 install |

---

## 5. 关键 commit 链(本轮)

```
4b24584 docs(claude): record the 2026-05-28 test infrastructure round + cosmology preset fix + bibcode-laundering guard
054e795 fix(claim-validator): plug bibcode/doi numeric-string laundering + classify assess_bao_bin_anomaly
70768c9 test(infra): 4-phase test-plan delivery — benchmarks / audits / corpus / security / blind-test / mockE2E / CI
f63bf0c fix(cosmology): seven code-review fixes + Planck chains fetch script
6be3f8b feat(chat): deterministic cosmology early-gate for Hubble tension / Alcock-Paczynski prompts
b857baf fix(chat): suppress research_plan_pending when cosmology direct-route matches
```

(完整 main 历史包含 ~15 个 fix/feat/docs/test commits)

---

## 6. 引用源

- 测试基础设施:`CLAUDE.md::Layered test infrastructure (2026-05-28)`
- 反幻造架构:`CLAUDE.md::Zero-fabrication architecture (Phase F core)` + `Citation-string laundering guard`
- 物理 preset 不变式:`CLAUDE.md::Cosmology preset / astropy alias (DO NOT regress)`
- Blind test cases:`backend/scripts/blind_test_cosmology_m0/cases.yaml` + `README.md`
- 早起闸门实现:`backend/app/api/chat.py::_cosmology_direct_route_from_prompt`
- V6/V10/V11 trace JSON:GitHub Actions artifacts(run id 26572308818 / 26584219705 / 26584788177)
