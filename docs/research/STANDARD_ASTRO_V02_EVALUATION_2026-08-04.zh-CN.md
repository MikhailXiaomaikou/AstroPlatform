# Standard Astro v0.2：灵活但可审计的轻量研究验证系统

## 摘要

本研究评估 Standard Astro v0.2 能否在保留模型研究灵活性的同时，把公开论文中的小型、可验证计算从重型 likelihood 工作流中分离。实验在实现前冻结 8 道任务，并对五个模型执行直接回答与 Standard Astro 两种条件、每题三次重复，共 `240/240` 个预注册样本。自动审计结果为：直接模型 `839/1440`（58.3%），Standard Astro `1440/1440`（100.0%），差值 `+41.7` 个百分点。自动发布门状态：**全部通过**。最初冻结的四模型 `192` 样本结果仍可单独复核；新增 Kimi K3 构成同题同口径的 48 样本扩展。该结论只适用于本题集和冻结评分规则；博士后 12 组匿名 A/B 复核尚须独立完成，不能以自动评分替代。

> **版本说明（2026-08-07）**：上述 Standard Astro `1440/1440` 是确定性管道与自动审计规则的自检，模型没有在回路，不能作为模型行为增益的头条证据。模型真实参与的修复后自然措辞层为 `651/720`（90.4%，n=60），详见战役报告。评分文件生成后，代码又完成十轮来源核验与路由加固；完整矩阵尚未在最新 `HEAD` 上重跑，所以本文数字是历史评测快照。

## 研究问题

1. 混合路由能否把明确表格计算送入轻量验证，而不因 DESI、BAO、CMB 等领域词误入完整研究矩阵？
2. 确定性工具能否同时保持数值、误差传播、单位、相关性和来源归因的可审计性？
3. 当来源超时、冲突或缺少跨数据集协方差时，系统能否保留合法算术并准确降级？
4. 相比裸模型，系统能否降低无依据数字和错误论文归因，同时不削弱能力缺口说明？

## 系统设计

v0.2 统一输出 `deterministic_source_check`、`research_exploration`、`full_research` 和 `general` 四种任务类型。高置信度轻量任务绕过模型生成代码，使用受控的 ratio、difference、product 或 inverse-covariance weighted mean，并以解析 Jacobian 传播一阶不确定性；奇异协方差不在 v0.2 定义域内，会明确拒绝。来源解析器支持 arXiv/ar5iv、arXiv 源/PDF、DOI 公开页面/PDF、Zenodo 官方附件、HTTPS URL 与带哈希缓存。来源匹配与派生数值分别授权，计算正确不会自动升级为“论文报告了该值”。

![总体评分](./assets/standard_astro_v02_overall.svg)

## 实验设计与评分

- 模型：`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`claude-fable-5`、`kimi-k3`。
- 条件：裸模型闭卷回答；Standard Astro 真实工具与门禁路径。
- 任务：8 道预注册观测宇宙学任务，每个实验单元重复 3 次。
- 评分：来源可追踪性、数值证据约束、不确定性校准、能力缺口处理、端到端成功、明显错误风险六维，每维 0–2 分，总分 12。
- 原始回答保存在忽略版本控制的 `.local/standard-astro-v02/evaluation_samples.jsonl`；仓库只保存可重算评分、汇总和图表。
- 自动规则审计不是专家评审；每条得分都保留维度值与异常标记，供复核者质疑。

## 结果

### 总体与模型

| 模型 | 直接条件 | Standard Astro |
|---|---:|---:|
| gpt-5.6-sol | 148/288 (51.4%) | 288/288 (100.0%) |
| gpt-5.6-terra | 140/288 (48.6%) | 288/288 (100.0%) |
| gpt-5.6-luna | 133/288 (46.2%) | 288/288 (100.0%) |
| claude-fable-5 | 214/288 (74.3%) | 288/288 (100.0%) |
| kimi-k3 | 204/288 (70.8%) | 288/288 (100.0%) |

![逐模型评分](./assets/standard_astro_v02_by_model.svg)

### 分任务结果

| 任务 | 直接条件 | Standard Astro |
|---|---:|---:|
| V02_01 DESI DR2 距离比 | 137/180 (76.1%) | 180/180 (100.0%) |
| V02_02 DESI 相关性敏感度 | 121/180 (67.2%) | 180/180 (100.0%) |
| V02_03 ACT DR6 H0 固定参照 | 133/180 (73.9%) | 180/180 (100.0%) |
| V02_04 ACT DR6 n_s 比较 | 145/180 (80.6%) | 180/180 (100.0%) |
| V02_05 Planck–SH0ES 锚点 | 81/180 (45.0%) | 180/180 (100.0%) |
| V02_06 Pantheon+ z=12 覆盖 | 34/180 (18.9%) | 180/180 (100.0%) |
| V02_07 DESI DR2 EDE 完整后验缺口 | 136/180 (75.6%) | 180/180 (100.0%) |
| V02_08 伪工具记录拒绝 | 52/180 (28.9%) | 180/180 (100.0%) |

![任务剖面（非时间趋势）](./assets/standard_astro_v02_task_profile.svg)

### 六维审计与状态

Standard Astro 的来源与数值证据两维联合达成率为 `100.0%`。120 个 Standard Astro 样本的终态构成为：`full`=60, `limited`=45, `refusal`=15。

![六维评分](./assets/standard_astro_v02_dimensions.svg)

### 延迟

- 轻量路径 P50：`0.011` 秒；P95：`0.088` 秒。
- 缓存命中 P50：`0.011` 秒；P95：`0.088` 秒。

![任务延迟](./assets/standard_astro_v02_latency.svg)

## 预注册发布门

| 自动检查 | 结果 |
|---|---|
| `formal_matrix_complete` | 通过 |
| `lightweight_route_accuracy_100pct` | 通过 |
| `expected_answer_hard_block_rate_zero` | 通过 |
| `unverified_numeric_or_attribution_escape_zero` | 通过 |
| `standard_score_at_least_85pct` | 通过 |
| `lead_at_least_5_percentage_points` | 通过 |
| `source_and_numeric_dimensions_at_least_95pct` | 通过 |
| `capability_gap_not_below_direct` | 通过 |
| `lightweight_p95_at_most_60_seconds` | 通过 |
| `cache_hit_p95_at_most_15_seconds` | 通过 |
| `desi_core_all_repeats_pass_five_science_checks` | 通过 |

自动门通过并不等于 Alpha v0.2 已完成发布：博士后匿名复核、72 小时开关观察以及发生严重错误时的回滚演练仍是独立门槛。

## 专家盲测

评测脚本从正式矩阵固定抽取 12 组匿名 A/B 对，覆盖五模型、完整回答、有限回答、能力缺口和伪证据。公开评审表位于 `STANDARD_ASTRO_V02_EXPERT_REVIEW_FORM.zh-CN.md`；隐藏条件与答案键只保存在 `.local`。专家目标为：严重科学错误 0，至少 10/12 可无需科学性修改作为研究起点，至少 8/12 优先选择 Standard Astro。

## 局限性

1. 8 道任务是有意选择的高价值微任务，不代表全部观测宇宙学。
2. 一阶 Jacobian 不适用于强非线性、非高斯或边界主导问题；这些问题必须升级到完整研究路径。
3. `verified_exact` 证明的是定位窗口中的标签和数值一致，不证明论文方法本身适用，也不等同于论文结论复现。
4. 来源抓取依赖公开网络与页面结构；付费墙和任意出版社爬虫不在 v0.2 范围。
5. 自动评分规则在运行前冻结但仍由项目方设计，必须保留专家盲评作为外部校准。

## 结论

v0.2 的判断标准不是“模型是否显得更聪明”，而是小型研究核查能否更快进入正确路径、留下可验算凭证，并在证据不足时给出有用而准确的边界。是否标记 Alpha v0.2 取决于当前代码上的矩阵复跑、上述自动门与独立专家门共同通过。

## 复现

```bash
cd backend
OPENAI_CLI_ENABLED=1 OPENAI_CLI_COMMAND=codex \
CLAUDE_CLI_ENABLED=1 CLAUDE_CLI_COMMAND=claude \
./venv/bin/python -m scripts.evaluate_standard_astro_v02
./venv/bin/python -m scripts.score_standard_astro_v02
MPLCONFIGDIR=/tmp/standard-astro-mpl \
./venv/bin/python -m scripts.render_standard_astro_v02_figures
./venv/bin/python -m scripts.build_standard_astro_v02_expert_pack
```
