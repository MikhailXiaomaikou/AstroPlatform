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
- [ ] **in-process 压缩路径的扩展模型名不副实(2026-06-12 新发现,设计决策——等用户拍板)**:in-process 路径从不采样 mnu(压缩核不响应)也从不采样 omegak(距离核 flat-only)——选 lcdm_mnu / ok_lcdm 跑压缩链得到与 lcdm 相同的结果但带着扩展模型名。该硬拦(扩展模型 + 纯压缩数据集 → 明确拒绝并指引 CMB 路径)还是软警告,定了再做。

## P2 — 数据/likelihood 保真度(模板成熟,性价比高)
- [ ] **fit_line_lfr 接 censoring/上限**:linmix 原生支持 censored data;ALPINE 有 ~43 个 [CII] 非探测带上限,现在被静默丢弃(给 Wu 的指南里已承认是已知限制)。接通后:上限行进 likelihood、结果声明 censoring 处理方式、选择偏差警告相应更新;golden 测试加上限行的端到端用例。科学敏感——必跑对抗审查。
- [ ] **注册表"external-only 但可廉价转 in-process"巡查**:按 ce1245f 的教训(DR2 = vendor-2-files 级),扫一遍 28 个 entry 里 execution_mode=external/config-only 的,凡 CobayaSampler 有现成钉得住的数据文件且 in-process 预测器已覆盖其 observables 的,列出来逐个接;没有的注明原因划掉。

## P3 — 防线与测试纵深
- [ ] **盲测 F 组扩容**:F1 只覆盖 LFR 链;给 A1(likelihood chain)和 abstention 路径各加一条特异度用例(干净回合不许被扣/降级),沿用 hard:true。
- [ ] **model_comparison 鲁棒矩阵的跨表示标记落地核查**:research_program 的矩阵格子在含 planck2018_compressed 时会出 comparison_valid=False——确认前端/报告对该字段的展示不误导,补一个端到端断言。
- [ ] **gate 事件首份周报**:积累一周事件后跑 triage,把 (gate, action) 分布和疑似误杀清单写进本文件,作为后续闸门调优依据。

## 已完成
- [x] **Planck 2018 lensing native 接入** (2026-06-12): CMBlikes native(smica consext8,9 bins)。vendor 最小集 1.3MB(5 文件 + 2 个窗口目录);窗口是 χ²-load-bearing(plik_lite bweight 教训),为此给 `_verify_pinned_cmb_data` 加了**目录聚合摘要**机制(sorted 文件名+字节,改/增/删/重命名任一窗口都翻红,机制本身有单测)。lensing 经 planck_calib 消费 A_planck → 进 CMB_APLANCK_KEYS;与 planck_pr4_lensing 互斥(同源 Planck 图)。锚点:−2lnL=8.82 / 9 bins(χ²/dof≈0.98,合发表值)。至此 clik-free Planck 2018 全套到齐:TT/TE/EE + lowE/lowT + lensing。验证: 56 目标测试 + smoke 4/4 + benchmarks 22/22 + audit 29 净 + 全量 2314 绿。
- [x] **ok_* 曲率模型在 CMB 路径** (2026-06-12): 双重实锤——`curved` 不是 CAMB 参数(CAMBUnknownArgumentError,所有 ok_* CMB 链在启动即死)且 omegak 从未进采样。修复 = 删伪参数 + omegak 进 CMB 参数序(先验 ±0.3)+ 把 w0→w 的别名机制泛化为 COBAYA_PARAM_ALIASES 表(omegak→omk)。物理判据:omegak=0 回 Λ 锚点 584.45,omegak=0.02 → 10992(声学峰对曲率极敏感),锁进回归测试。验证: smoke 4/4 + benchmarks 22/22 + audit 净 + 全量 2307 绿。
- [x] **mnu 在 cobaya CMB 路径缺失** (2026-06-12, commit 见下): 活体实锤——*_mnu 模型的 CMB 参数序无 mnu,链静默跑 CAMB 默认固定质量却标 mnu 模型名(比 w0 孤儿更险:静默冒名)。修复 = 参数序追加 mnu + CMB_PARAMETER_PRIORS 加 (0.0, 5.0) eV 平先验(放 CMB 表防 in-process 误拾)。物理判据:plik_lite 对 mnu 强响应(-2lnL 584→2044 @ 0.06→0.5 eV),已锁进回归测试。验证: smoke 4/4 + benchmarks 22/22 + audit 28/28 + 全量 2305 绿。
- [x] **Daily cron 失班处置** (2026-06-12): 11 日 16:00 UTC 班次被 GitHub 调度器丢弃(整点 cron 拥挤的已知行为,历史实跑已漂到 +2.4h);已手动补跑(run 27360552699,兼 F1 的 CI 首秀)并把 cron 错峰到 16:17 UTC(commit 8dc7987,**需推送生效**)。
