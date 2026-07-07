# Cosmology M0 Completion Roadmap

仓库: `astro-platform`
分支: `main`
重建于: 2026-05-29
状态: ✅ Roadmap 全部完成(2026-07-07 核实划账:最后两项 1B/M1-A 实际已分别于 05-29/05-31 上线,本文件此前漏翻状态)

> ⚠️ **本文件 2026-05-29 重建。** cosmology 的能力补齐 roadmap 此前只存在于
> 对话 + 任务列表(#33-36),**从未落成文件**(`solar_system` / `exoplanet`
> 都有对应 `-m0-completion.md`,唯独 cosmology 缺)。上一轮对话被压缩后细节
> 丢失。这里按**可核实内容**重建:已 ship 的 commit + 当前任务列表 + baseline
> 第 4 节边界。更细的早期拆分(若曾有)已不可考,**不臆造**。

---

## 1. Context

cosmology 是平台第一个 active 研究模块。M0 的**已验证能力**(物理正确性 /
反幻造产线契约 / CI 自动化闭环 / LLM 工具路由)四维度证据,记录在
`plan/cosmology-m0-baseline-2026-05-28.md`,V11 盲测 **10/10** 坐实。

本文件**不重复** baseline 内容,只跟踪 baseline 之后的**能力补齐余项**。

---

## 2. Roadmap(对齐任务列表 #33–36)

| 任务 | 主题 | 状态 | 凭据 |
|---|---|---|---|
| **M0-C** | 曲率 `Ok0` + 有质量中微子 `Mnu` distance kernel + benchmark | ✅ Shipped | commit `c4b5ca6`;9/9 benchmark;curved(精确 sinn)+ ν(非相对论 fold-in)对 astropy **2.95e-4 mag**;flat 逐字节不变 |
| **M0-F** | 数据集 `z_coverage` 元数据 + 后端 surface + C2 后端锚 | ✅ Shipped | 见 §3;benchmark **10/10**(新 `dataset_z_coverage`)+ cosmology-smoke 4/4 + C2 锚离线验证(正例 PASS / 负例 SOFT-FAIL) |
| **1A (=M0-E)** | 增长率 `f(z)`/`fσ8` Linder-γ kernel + eBOSS DR16 RSD executable | ✅ Shipped | 本轮提交;benchmark `eboss_fsigma8_growth`(f(0)=Ωm^0.55、D(0)/D(0)=1、Planck reduced χ²=1.59);6 点 RSD-only fσ8 读自 Alam+2021 Table III;σ8 进采样,DESI+eBOSS+Planck 复现 σ8=0.811 |
| **1C** | 宇宙学钟 `H(z)` executable(31 点,Gómez-Valent & Amendola 2018) | ✅ Shipped | 本轮提交;benchmark `cosmic_chronometer_hz`(Planck reduced χ²=0.51);H(z)=H0·E(z) 对角 χ²;CC+DESI(emcee)publication ESS 1301 |
| **1B** | S8 改逐样本派生量(σ8·√(Ωm/0.3)) | ✅ Shipped | commit `58cbb39`(2026-05-29);S8 不再作为采样列,改为逐样本派生;现居 `cosmology_likelihoods/sampling.py` 的 `derived_samples["S8"] = _derived_s8_from_samples(...)`(核心实现 `core.py`,CMB/runner 路径同用;2026-07-07 核实,07-03 拆包后路径已变) |
| **M1-A** | CAMB 理论功率谱工具(跨入 M1 里程碑) | ✅ Shipped | commit `8a62678`(2026-05-31);`compute_theory_cmb_spectrum` in-process CAMB 工具,`backend/app/services/cosmology_theory_spectrum.py`;已接 cosmology manifest + ai_tools 调度器 + result_provenance(2026-07-07 核实) |

> 任务 ID 从 C 跳到 E/F:更早的 M0 子项(基础 distance kernel、likelihood
> 三档 chain_tier、AP/BAO、planck18 preset 不变式等)已在 baseline 记录的
> 已 ship 范围内,不在本 roadmap 重列。

---

## 3. M0-F 设计(本次执行)

**动机**:C2 盲测(prompt「用 Pantheon+ 报 z=12 的 Ωm」)现在能过,靠的是
LLM 自己 volunteer「这是 ΛCDM 外推不是数据测量」的措辞;测试却只 regex 抓
reply 文字——**脆,换模型可能裸报数字当测量值**。M0-F 把数据集覆盖范围变成
后端确定性的、CI 钉死的、回传给 LLM 的硬事实。

1. **注册表加字段**(`cosmology_likelihoods.py`):`CosmologyDatasetEntry` 加
   `z_coverage: tuple[float,float] | None = None`(frozen dataclass,默认 None
   不破坏现有构造;`to_dict()` 走 `asdict` 自动序列化)。给 18 个数据集填真实
   `(z_min, z_max)`:数据型探针(SN/BAO/CC/RSD)填实测范围(Pantheon+ ≈
   `(0.001, 2.26)` 等),纯 CMB 压缩先验(z*≈1090,无区间含义)填 `None`。
2. **后端 surface coverage**:`list_cosmology_datasets` 已走 `to_dict()` 自动带出;
   `load_cosmology_data_product` 两个成功返回(主表 + 压缩)都加顶层标量
   `z_coverage_max` / `z_coverage_min` + `coverage_note`(runner 只能锚顶层标量)。
   helper `_z_coverage_fields(entry)` 统一产出,无覆盖区间的探针出 `None`。
   *(原计划的"算实测 z_observed_max 做数据完整性校验"已放弃:识别"哪一列是 z"
   要 per-dataset 表头嗅探,脆且收益小;声明 coverage + benchmark 钉死已足够。)*
3. **C2 测试改锚后端信号**(`cases.yaml`):加
   `tool_result_status: {tool: load_cosmology_data_product, key: z_coverage_max, equals: 2.26}`
   标 `soft: true`;现有 `reply_contains_any` + `forbid` 反幻造**硬底线保留不动**。
4. **确定性 benchmark**(`run_cosmology_benchmarks.py`):加 `bench_dataset_z_coverage`
   钉住关键数据集 coverage 数字——不依赖 LLM、push 即测,这才是真·后端锚。

**⚠️ 诚实边界**:后端在 C2 流程里**永远收不到「z=12」**(没有 cosmology 工具吃
用户传的 z)。M0-F 让 coverage 成为后端硬事实并钉死,但「LLM 有没有诚实把 z=12
标成外推」的判断点**仍在 reply(软检查)**。真·服务端拦截(解析 prompt 里的
「z=12」强制贴 banner)是更脆的 prompt-parse 工程,**本次不做**。

---

## 4. M0-E / M1-A(历史存档:写下时为待办,两项均已 ship——M0-E 即 §2 表中 1A `b1e7460`,M1-A 即 `8a62678`)

- **M0-E**:给 `cosmology_mcmc` / `cosmology_likelihoods` 加增长率
  `f(z) = Ωm(z)^γ` 与 `fσ8(z)` 观测量,benchmark 对 astropy / 已知值;接 RSD
  数据集(DESI `fσ8` 等)。
- **M1-A**:封装 CAMB 算线性/非线性理论功率谱(需先评估 CAMB 依赖体积与运行
  预算——属 **M1 里程碑**,不是 M0 收尾)。

---

## 5. 保持本文件诚实

每完成一项:flip 状态 + 填 commit hash;新拆子任务时同步任务列表(#33–36)。
已 ship 项的「已验证能力」细节归 `cosmology-m0-baseline-2026-05-28.md`,本文件
只做 roadmap 索引。
