# 观测宇宙学完善 backlog(自主 loop 工作清单)

维护规则:loop 每轮取最上面一条未完成项 → 实现 → 全量验证 → 本地 commit(**不推送**,推送由用户决定)→ 在此文件划账(状态/commit hash/一句结论)→ 报告。
新发现的工作加到对应优先级段落,带一行理由。完成项移到底部"已完成"段。

每轮开工前的固定动作:
1. `gh run list --workflow=daily.yml --limit 1` 看昨日盲测;红了→优先调查,绿了→继续。
2. `venv/bin/python scripts/triage_gate_events.py` 扫 gate 事件;出现新的疑似误杀→优先级提到最前。
3. 工作区不干净或全量套件起点不绿 → 停下报告,不动工。

验证门(每项必过才许 commit):后端全量 pytest + ruff(改动文件)+ benchmarks 22/22 + audit_registry 干净;动了 `cosmology_*.py` 加跑 /cosmology-smoke;科学关键改动跑对抗审查 workflow。
新数据集/升级 likelihood 走 /add-dataset checklist,一步不跳。

## 红线(不经用户点头不准碰)
- ACT DR6 主谱 mflike(独立包 + ~20 前景 nuisance;北极星笔记定性为兔子洞)
- WL 3x2pt 完整 likelihood(KiDS/DES/HSC 外部包级工程)
- 任何新垂直 / 平台架构级改动 / 推送 origin

## P1 — 科学正确性缺口(先核实再修,核实结果写回这里)
- [ ] **mnu 在 cobaya CMB 路径缺失(疑似)**:`_cobaya_parameter_order` 的 CMB 分支只追加 w/w0/wa;`lcdm_mnu`/`w0wa_cdm_mnu` 选 CMB entry 时 mnu 疑似根本不进采样(与已修的 w0 孤儿同类)。先活体复现(get_model 探针,照 test_planck_lowl 的 w0wa 测试模式),真则修+回归测试。
- [ ] **ok_* 曲率模型在 CMB 路径(疑似)**:`_model_theory_args` 对 ok_* 设 `args["curved"]=True` —— "curved" 疑似不是合法 CAMB extra_arg(对照 camb.set_params 签名),且 omegak 不在 CMB 参数序里。活体探针核实,真则修。

## P2 — 数据/likelihood 保真度(模板成熟,性价比高)
- [ ] **Planck 2018 lensing native 接入**:cobaya 自带 `planck_2018_lensing.native`(clik-free;离线 w0wa 复现用过它)。照 39e21bf 低-l 的完整模式:cobaya-install 拉数据 → vendor + sha256 钉死 → 注册 entry + 适配器 + `_CMB_PINNED_DATA` → best-fit 锚点复现测试。完成后平台拥有 TT/TE/EE+lowE+lensing 全套。
- [ ] **fit_line_lfr 接 censoring/上限**:linmix 原生支持 censored data;ALPINE 有 ~43 个 [CII] 非探测带上限,现在被静默丢弃(给 Wu 的指南里已承认是已知限制)。接通后:上限行进 likelihood、结果声明 censoring 处理方式、选择偏差警告相应更新;golden 测试加上限行的端到端用例。科学敏感——必跑对抗审查。
- [ ] **注册表"external-only 但可廉价转 in-process"巡查**:按 ce1245f 的教训(DR2 = vendor-2-files 级),扫一遍 28 个 entry 里 execution_mode=external/config-only 的,凡 CobayaSampler 有现成钉得住的数据文件且 in-process 预测器已覆盖其 observables 的,列出来逐个接;没有的注明原因划掉。

## P3 — 防线与测试纵深
- [ ] **盲测 F 组扩容**:F1 只覆盖 LFR 链;给 A1(likelihood chain)和 abstention 路径各加一条特异度用例(干净回合不许被扣/降级),沿用 hard:true。
- [ ] **model_comparison 鲁棒矩阵的跨表示标记落地核查**:research_program 的矩阵格子在含 planck2018_compressed 时会出 comparison_valid=False——确认前端/报告对该字段的展示不误导,补一个端到端断言。
- [ ] **gate 事件首份周报**:积累一周事件后跑 triage,把 (gate, action) 分布和疑似误杀清单写进本文件,作为后续闸门调优依据。

## 已完成
(loop 划账区)
