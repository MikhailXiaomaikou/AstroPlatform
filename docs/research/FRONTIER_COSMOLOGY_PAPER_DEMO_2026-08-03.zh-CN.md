# Standard Astro 前沿论文测试提案与初步结果

**主题：** 从近期观测宇宙学论文抽取一个公开、可验证的小型系统任务

**研究日期：** 2026-08-03

**评估对象：** `gpt-5.6-sol` 直接模型研究 vs. 同模型通过 Standard Astro 研究
**状态：** 初步 A/B 已完成；未改动产品功能，未提交或推送

## 技术摘要

**建议把 DESI DR2 的单红移箱 BAO 距离比误差传播作为下一版前沿论文 demo。** 所选来源是 DESI Collaboration 的 *DESI DR2 Results II: Measurements of Baryon Acoustic Oscillations and Cosmological Constraints*。论文于 2025-10-06 正式发表，完整补充数据已公开。任务只要求用论文表 4 中 LRG2 红移箱的 `D_M/r_d`、`D_H/r_d` 及二者相关系数，独立重算 `D_M/D_H` 和一阶误差；它不要求重做 BAO 拟合、MCMC 或暗能量模型比较。

地面真值复核得到 `D_M/D_H = 0.891852994`、`σ = 0.020562805`，舍入后与论文的 `0.892 ± 0.021` 一致。固定容差 `|Δratio| ≤ 0.001`、`|Δσ| ≤ 0.001` 均通过。负相关 `ρ=-0.404` 会增大比值误差；若错误忽略相关性，误差会从正确的 `0.020562805` 低估为 `0.017652837`。

初步 A/B 出现明确反例：直接模型约 30 秒完成并通过全部五项验收；Standard Astro 约 173 秒后虽然传输层完成且没有硬拦截，却没有返回任何所求计算。其最早可观察偏离是把表格算术题路由到 `plan_research_program` / `run_research_matrix`；论文表提取随后超时，最终又被“非 publication-ready posterior”门降级为与本题无关的后验扣留文案。这个结果说明，当前优先级不是放宽科学门禁，而是增加“已给定、可审计表格上的确定性派生量”路径，并让提取失败降级为“有限计算 + 证据缺口说明”。

## 研究问题与成功标准

本报告回答四个问题：

1. 近期哪些 BAO、CMB 或早期暗能量论文同时具备公开数据、可审计主张和较好的系统适配度？
2. 能否把一篇大论文缩减为几分钟内可重复、无需重跑完整分析的小任务？
3. 在同一模型、同一提示下，直接条件和 Standard Astro 条件能否给出正确数值、来源和能力边界？
4. 如果失败，最早可观察失败节点在哪里，最小修复方向是什么？

任务级成功不是“回答更长”，而是同时满足：数值正确、相关误差正确、来源可追踪、边界说明准确、没有把算术检查冒充论文复现。

## 三篇候选论文均来自最近十二个月的正式发表版本

筛选范围为 2025-08-03 至 2026-08-03 的观测宇宙学正式论文，优先使用论文官网、合作组数据页、NASA 数据库与 Zenodo 等一手来源。

| 候选 | 正式发表日期与来源 | 公开数据 / 代码 | 可复现性 | Standard Astro 适配度 | 主要风险 |
|---|---|---|---|---|---|
| **DESI DR2 Results II：BAO 测量与宇宙学约束** | 2025-10-06；[Physical Review D](https://journals.aps.org/prd/abstract/10.1103/tr6y-kpc6)，[arXiv:2503.14738](https://arxiv.org/abs/2503.14738) | [Zenodo 补充数据，DOI 10.5281/zenodo.16644577](https://doi.org/10.5281/zenodo.16644577)；官方说明称可复现论文全部图表和数值 | **高（小任务）/ 中（整篇）**：表 4 可直接审计；整包约 1.3 GB，完整宇宙学链明显更重 | **高**：与现有 DESI/BAO、文献表格、证据图和声明门禁直接相邻 | 系统当前注册数据以 DESI DR1 为主；DR2 表提取和来源注册不完整，容易误转全似然路径 |
| **ACT DR6：功率谱、似然与 ΛCDM 参数** | 2025-11-19；[JCAP DOI 10.1088/1475-7516/2025/11/062](https://doi.org/10.1088/1475-7516/2025/11/062)，[arXiv:2503.14452](https://arxiv.org/abs/2503.14452) | [ACT DR6 数据产品页](https://act.princeton.edu/act-dr6-data-products)、[NASA LAMBDA DR6.02](https://lambda.gsfc.nasa.gov/product/act/act_dr6.02/index.html)、公开 full / lite likelihood 与教程 | **高（官方资产完整）/ 低至中（本机成本）**：数据、chains、likelihood 齐全，但真实 CMB 似然运行依赖较重 | **中**：主题契合 CMB/H0；现有产品更擅长压缩约束与证据审计，不是 ACT DR6 全似然复现器 | 容易把公开参数表核验升级成耗时的 CMB likelihood；foreground 与数据组合定义复杂 |
| **DESI DR2 的早期暗能量替代解释** | 2025-09-25；[Physical Review D DOI 10.1103/xtql-wh3h](https://doi.org/10.1103/xtql-wh3h)，[arXiv:2503.24343](https://arxiv.org/abs/2503.24343) | [Zenodo 补充材料，DOI 10.5281/zenodo.15185439](https://doi.org/10.5281/zenodo.15185439) | **中**：补充包仅约 380 kB，公开且易取得；但论文主结论仍依赖多数据联合推断 | **中低**：与 EDE/H0 主题高度契合，但正好落在现有“完整 EDE likelihood 不可执行”的已知能力缺口 | 最容易诱发“用压缩近似冒充完整 EDE 联合分析”或只返回笼统拒答；不适合作为首个绿色 demo |

**选择结论：** DESI DR2 Results II 胜出。它同时提供近期正式版本、稳定论文标识、公开补充数据、与现有 BAO 工具邻近的输入，以及一个无需下载 1.3 GB 数据包就能由论文正文交叉验证的小任务。ACT DR6 更适合后续 CMB 数据产品读取 demo；EDE 论文更适合作为“明确能力缺口如何说明”的红队样例，而不是首个正常通过样例。

## 冻结的 demo 任务

### 来源包

来源：DESI Collaboration, *DESI DR2 Results II*, arXiv:2503.14738，表 4。LRG2 的有效红移与测量为：

- `z_eff = 0.706`
- `D_M/r_d = 17.351 ± 0.177`
- `D_H/r_d = 19.455 ± 0.330`
- `ρ(D_M/r_d, D_H/r_d) = -0.404`
- 论文列出的 `D_M/D_H = 0.892 ± 0.021`

这些数值已逐项对照论文表 4；没有从摘要或模型记忆补写。

### 要求

令 `x=D_M/r_d`、`y=D_H/r_d`、`R=x/y`。计算

```text
R = x / y
σ_R² = R²[(σ_x/x)² + (σ_y/y)² - 2ρ(σ_x/x)(σ_y/y)]
```

然后与论文舍入值比较，并说明负相关的方向性影响。

### 预注册验收条件

1. 报告 `R`，并满足 `|R - 0.892| ≤ 0.001`。
2. 使用完整相关误差传播，报告 `σ_R`，并满足 `|σ_R - 0.021| ≤ 0.001`。
3. 正确说明：对比值 `x/y`，负相关使两项扰动对比值的影响强化，因此误差比假设独立时更大。
4. 至少引用 `arXiv:2503.14738` 和表 4。
5. 明确说这只是已发表表格的算术一致性检查，不是 DESI BAO 拟合、似然或暗能量结论的复现。

## 可独立复算的地面真值

从冻结输入直接得到：

| 项目 | 复算值 | 论文/容差 | 终态 |
|---|---:|---:|---|
| `R = 17.351/19.455` | 0.891852994 | 0.892 | 通过 |
| 含 `ρ=-0.404` 的 `σ_R` | 0.020562805 | 0.021 | 通过 |
| `|ΔR|` | 0.000147006 | ≤ 0.001 | 通过 |
| `|Δσ|` | 0.000437195 | ≤ 0.001 | 通过 |
| 假设 `ρ=0` 时的误差（反事实校验） | 0.017652837 | 不应作为正式误差 | 正确值较其高约 16.5% |

该计算只使用论文表格四个输入和一阶误差传播，没有调用宇宙学模型，也没有对暗能量作推断。

## A/B 初步实验方法

- **模型：** `gpt-5.6-sol`
- **重复数：** 每条件一次；本报告只作描述性结论
- **固定提示：** 两条件均收到完全相同的来源包、公式任务、容差和边界要求
- **直接条件：** 温度 0，无外部工具；系统仅要求闭卷诚实回答，不得假装查询或运行似然
- **Standard Astro 条件：** 同一模型标识；使用当前生产研究提示、真实工具白名单、代理循环、证据/数值/引用门；最多 5 轮、240 秒
- **敏感信息处理：** 报告只保留最终用户可见回答和紧凑工具/门禁状态，不保存或回显系统提示、凭据与原始 trace

本设计有意把论文所需输入放进固定来源包，使两条件都能完成数学计算。它测量的是“确定性计算、来源映射和边界控制”，不是模型能否凭记忆找出表格。

## 初步结果：直接条件通过，Standard Astro 发生路由型失败

| 条件 | 耗时 | 用户可见终态 | 五项验收 | 安全性 |
|---|---:|---|---:|---|
| 直接模型 | 30.028 秒 | 给出公式、`0.891853 ± 0.020563`、两项差值、负相关解释和论文表号 | **5/5 通过** | 未见新增或不受支持的数值；边界说明正确 |
| Standard Astro | 172.998 秒 | 只返回“后验不是 publication-ready，因此扣留数值并建议运行 full likelihood” | **0/5 通过** | 没有伪造数值，也不是硬拦截；但回答对象错误、不可用 |

直接条件的舍入结果是 `0.892 ± 0.021`，与地面真值一致。它还正确说明负相关增加比值误差，并明确声明没有复现 BAO fit 或暗能量推断。

Standard Astro 的紧凑终态为：

- `plan_research_program`: completed
- `run_research_matrix`: partial
- `build_evidence_graph`: partial
- `extract_literature_tables`: failed，`tool_timeout`
- 两次 `load_cosmology_data_product`: completed
- `export_research_report`: completed
- 数值门：`regenerated`
- 引用门：`skipped`
- 再生次数：1
- `blocked=false`、`limited=false`
- 命中迭代上限，未命中总时限
- 降级原因包含 `research_mode_deterministic` 和 `posterior_values_withheld`

因此应把 Standard Astro 记为**传输完成、任务级失败**，不能因为 `status=completed` 而计作端到端成功。

## 失败路径：已验证事实与推断

### 已验证

1. 第一项工具不是确定性算术或表格核验，而是 `plan_research_program`；随后进入研究矩阵和证据图。
2. `extract_literature_tables` 没有取得表 4，终态为 `tool_timeout`；运行日志同时记录 arXiv 压缩流提前结束。
3. 后续加载了宇宙学数据产品，但没有生成本题要求的比值和误差。
4. 最终用户文案谈论“posterior values”和“full-likelihood path”，而任务明确说不需要 posterior 或完整 likelihood。
5. 科学门禁没有放出虚构数值，`blocked=false`；但 `limited` 也没有被标记，用户无法从状态上区分“安全但无关的扣留文案”和“有用的有限回答”。

### 推断

1. **最早可观察的路由偏差**是 `plan_research_program`。提示中的 DESI、BAO 和暗能量边界词可能压过了“只做表格误差传播”的任务形态；这是根据工具顺序作出的产品推断，不是对模型内部心理过程的证明。
2. 表格提取失败本身不必导致零回答，因为固定来源包已经给出全部计算输入。当前声明门没有“用户提供但尚未独立抓取验证的来源包”这种可用降级语义，于是选择了与后验相关的固定扣留摘要。
3. 最终“posterior withheld”表明降级模板按运行过的工具类别选择，而不是按用户原始任务选择。需要用更具体的门禁事件或路由原因字段验证这一推断。

## 三段根因定位：轻量算术如何被送进完整研究链

下面的定位来自对当前共同主 checkout 的只读代码审查，以及本次真实运行留下的紧凑工具状态。三段依次相连；第一段已经足以解释为什么系统没有直接做算术，后两段解释为什么它最终给出了与问题无关的扣留文案。

### 第一段：分类器把“论文表格一致性检查”误判为完整宇宙学研究

**哪里错：** `backend/app/services/agent_runtime/prompt_routing.py` 的 `_inline_statistics_tool_call_from_prompt` 只识别带两组数组的回归/统计提示，不识别“两个标量、各自误差、相关系数、求比值误差”这一任务形态。本题因此没有得到轻量统计调用。紧接着，`_is_cosmology_likelihood_workflow` 通过宽泛的关键词交集判断是否进入宇宙学 likelihood：提示同时出现 DESI/BAO、`covariance`/`dark energy` 和 `compare`/`test` 等词，就会返回真。它没有理解“**不是**暗能量推断、**不要**重跑 likelihood”这种否定边界。`_is_research_program_workflow` 又在这个判断之上把 `compare`、`test`、`analysis` 视为研究计划信号。

对冻结提示直接调用这些分类函数的可复现结果是：

```text
cosmology_likelihood = true
research_program     = true
inline_statistics    = none
direct_route         = none
dataset_keys         = [desi_dr2_bao]
models               = [lcdm, wcdm, w0wa_cdm]
```

其中 `_cosmology_models_from_prompt` 还把否定语境中的“dark-energy inference”解释成需要 ΛCDM、wCDM、w0waCDM 三模型比较。另一个直接路由函数 `_cosmology_direct_route_from_prompt` 只列出 `dm/dh ratio`、`alcock-paczynski` 等少数文本别名，没有覆盖提示中的 `D_M/D_H` 记法；而即使命中，它指向的 `assess_bao_bin_anomaly` 也是 DESI DR1 的 AP/Ωm 异常诊断，并不是 DESI DR2 已发表表格的标量相关误差传播。因此，现有直接路由既“没有认出写法”，也“没有合适的轻量目标”。

**为什么错：** 当前分类主要按领域词和动词计数，而不是先判断任务形状：输入是否已经完整、目标是否是确定性派生量、用户是否明确排除拟合/后验。论文标题和边界说明反而把一个四则运算题推向最重路径。这是已由函数输出验证的路由错误，不是对模型偏好的猜测。

**最小修复边界：** 在 `_is_research_program_workflow` 之前增加一个窄的 `source_packet_scalar_check` 分类：只接受来源标识、标量 `x, σx, y, σy, ρ` 和比值/差值/线性组合目标；规范化 `D_M/D_H`、Unicode 和 LaTeX 别名；把“不要拟合/likelihood/posterior/暗能量推断”作为任务形态排除信号。现有 `astro_statistics_toolbox` 只有 summary、regression、bootstrap regression 和 censored summary，不能诚实承接本题；最低风险的功能边界是给它新增一个确定性的 `correlated_ratio` 分析类型，或增加同等狭窄的新工具，而不是复用 DESI DR1 的 `assess_bao_bin_anomaly`。

### 第二段：运行时强制研究矩阵，表格抓取又受默认 45 秒包络限制

**哪里错：** `backend/app/services/agent_runtime/loop.py` 在上述布尔分类之后计算 `research_plan_pending`、`research_matrix_pending` 和 `research_evidence_pending`。只要研究计划路由为真且没有直接路由，它就依次把模型可见工具收缩为 `plan_research_program`、`run_research_matrix`、`build_evidence_graph`，并注入“这是唯一可用工具”的运行时要求。于是本次前三步不是模型自由选择的研究风格，而是运行时确定性强制的重路径。

随后 `extract_literature_tables` 在 `backend/app/services/agent_runtime/tool_execution.py` 中没有专用时限，继承 `_TOOL_DEADLINE_DEFAULT = 45.0` 秒；超时后外层取消任务并返回 `error_class=tool_timeout`。表格连接器 `backend/app/api/arxiv.py` 会尝试 ar5iv，再抓取 arXiv e-print；本次日志明确记录 e-print 压缩流提前结束。`backend/app/services/ai_tools/literature_tables.py` 虽有重试包装，但它仍处于同一个外层工具时限和整条研究链中。

**已验证与未知边界：** 已验证的是：运行时按固定顺序强制了计划/矩阵/证据图；表格工具使用 45 秒默认包络；终态是 `tool_timeout`；日志出现压缩流损坏。仅凭紧凑 trace 不能证明 45 秒全部消耗在下载、解压、解析或重试中的哪一个微步骤，因此本报告不把某个内部子步骤伪称为唯一超时原因。

**最小修复边界：** 首要修复不是简单提高超时，而是让输入完整的表格算术根本不进入研究矩阵。对确实需要抓表的请求，再为表格工具设置与“元数据 → ar5iv → e-print → 解析”相匹配的显式预算，下载后先验证压缩载荷，失败时切换官方 DOI HTML、已登记表格缓存或其他可审计来源。若用户给定的来源包已含全部数字，抓取失败应只降低“来源已被系统二次确认”的等级，不能抹掉仍可验证的算术。

### 第三段：研究摘要中的后验数值触发了与原任务无关的最终拒绝模板

**哪里错：** `loop.py` 先用 `_research_tool_grounded_summary` 把研究矩阵结果整理成确定性摘要。最终 F2 门又检查 `nonpublication_posterior_values`；一旦研究矩阵带出的内容被视为不可发布后验，就尝试生成宇宙学工具摘要，否则调用 `nonpublication_posterior_refusal()`。`backend/app/services/agent_runtime/summaries.py` 的宇宙学摘要只认识注册表、配置、chain、AP 和 H0 anchor 等特定结果，不认识本次通用的 `run_research_matrix` / `load_cosmology_data_product` 组合，因此回落到 `backend/app/services/agent_runtime/honesty.py` 中固定的“posterior values are withheld / run full-likelihood path”文案。

**为什么错：** 最终门禁依据“错误重路径产生了什么中间对象”选择文案，而没有再次检查用户原始任务是不是后验任务。本题本来只求 `D_M/D_H` 的算术结果，却因上游矩阵产生了后验语义，最后被后验发布规则接管。门禁正确避免了发布未经批准的后验数值，但错误地把无关拒绝当成整题终态。

**最小修复边界：** 最终回退必须同时读取 `requested_task_kind`。当它是 `deterministic_table_check` 时，即使旁路研究矩阵出现不可发布后验，也只剔除这些无关后验，并返回：`arithmetic_verified=true`、`source_packet_user_supplied=true`、`source_independently_refetched=false`、`source_fetch_error=tool_timeout`，附一句“计算已核验；本轮未能独立重抓来源表格”。同时增加 `selected_route`、`first_route_mismatch` 和 `fallback_available` 观测字段。这样保留科学证据门禁，同时把真正硬拦截与可用的有限回答清楚分开。

**一句话解释：** 系统不是算不出，而是先被关键词送进了不必要的完整研究流水线；抓表失败后，它又根据流水线里的“后验”而不是用户的“表格算术”选择了拒绝模板。正确做法是先识别这是一个有完整输入的轻量确定性计算，算出结果，并把来源二次核验失败单独标注为证据缺口。

## 最小改进方向（本次不实施）

1. **增加确定性派生量任务类别。** 当输入是明确的论文表格数值、公式目标是比值/差值/误差传播且没有要求拟合时，优先走受限 Python/统计计算与来源绑定，不启动研究矩阵或 likelihood。
2. **把“提取失败但输入齐全”降级为有限回答。** 可以计算并标注“数值来自用户提供的固定来源包；本轮独立抓取表格超时，因此算术已验证、来源内容未由工具二次确认”。这比完全扣留更诚实也更有用。
3. **不要复用 posterior 扣留文案。** 非后验任务不应出现 `posterior_values_withheld`。降级摘要至少要回到原任务类型、缺失证据和仍可完成的部分。
4. **补三个观测字段。** 建议记录 `requested_task_kind=deterministic_table_check`、`earliest_route_mismatch=plan_research_program`、`fallback_available=user_source_packet_arithmetic`。这能区分科学门禁正确阻止主张与路由错误导致的过度保护。
5. **保持科学门禁不变。** 不建议因为本例失败而允许未经来源映射的数值直接成为“已验证文献主张”；应通过分层标签把“计算正确”“来源包给定”“工具已二次确认”分开。

## 局限性、不确定性与稳健性

- A/B 只有一个模型、每条件一次，不能估计模型随机性或总体效应。
- 固定来源包使直接条件不需要检索；因此本实验不能评价文献发现能力，只评价计算、来源标注和系统路由。
- 本报告逐项核对了论文正文表 4，但没有下载 1.3 GB Zenodo 包，也没有复现 DESI clustering、BAO fit 或宇宙学 chains。
- Standard Astro 的表格提取遇到一次压缩流异常。尚未用重复运行区分瞬时网络问题与稳定解析缺陷；不过即使提取失败，错误转入 posterior 扣留文案仍是已验证的终态问题。
- 直接条件和 Standard Astro 条件的上下文不等长；这与既有四模型评估相同，是“模型独立完成”与“模型通过系统完成”的处理差异。
- 报告没有画图：每个条件只有一个离散样本，表格能更准确表达终态；此时绘制柱图会夸大证据量。

## 推荐下一步

1. 把本任务加入固定回归集，但先保留为预期失败，避免把当前结果误标为系统成功。
2. 完成最小路由/降级修复后，先用同一提示重跑 `gpt-5.6-sol`；目标是五项验收全部通过，或至少返回明确的“算术通过、来源二次抓取失败”有限回答。
3. 再扩展至既有四模型，每个条件重复 3–5 次；报告任务级成功率、来源映射率和无关扣留文案率，不以回答长度计分。
4. 作为第二阶段，再从 ACT DR6 抽取一个公开 chain 摘要核验题，并把 DESI EDE 论文保留为“完整 likelihood 不可执行时的高质量缺口说明”样例。

## 复现实验说明

独立计算只需以下等价代码：

```python
import math

x, sx = 17.351, 0.177
y, sy = 19.455, 0.330
rho = -0.404
ratio = x / y
sigma = ratio * math.sqrt(
    (sx / x) ** 2 + (sy / y) ** 2
    - 2 * rho * (sx / x) * (sy / y)
)
```

模型 A/B 复用仓库现有 `backend/scripts/evaluate_standard_astro_ab.py` 中的 `_run_direct` 与 `_run_standard` 条件定义，只替换为本报告冻结的单一提示；未把生产系统提示、密钥或原始 trace 写入结果。复现时应记录模型标识、温度、最大轮数、总时限、最终用户可见答复、紧凑工具状态和门禁摘要，并继续把 `status=completed` 与任务级成功分开。

## 结论

DESI DR2 LRG2 距离比是一个比“复现暗能量显著性”更适合 Standard Astro 当前阶段的前沿论文 demo：公开、近期、可审计、计算短、成功标准明确，而且能检验相关误差而不鼓励过度外推。初步直接模型已经证明该任务本身清楚可做；Standard Astro 的失败不是科学上无法回答，而是表格算术被过度路由、提取失败后又套用了错误的 posterior 扣留模板。最小方向应是“确定性计算 + 分层来源状态 + 有用的有限回答”，而不是降低证据门槛。
