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
(暂空 — MGS 与 Union3 均已完成,见已完成段)

## P3 — 防线与测试纵深
- [ ] **model_comparison 鲁棒矩阵的跨表示标记落地核查**:research_program 的矩阵格子在含 planck2018_compressed 时会出 comparison_valid=False——确认前端/报告对该字段的展示不误导,补一个端到端断言。
- [ ] **gate 事件首份周报**:积累一周事件后跑 triage,把 (gate, action) 分布和疑似误杀清单写进本文件,作为后续闸门调优依据。

## 已完成
- [x] **盲测 F 组扩容** (2026-06-12, commit 1c8c821): 11→13 用例。F2_likelihood_chain_specificity (hard): 旗舰 likelihood-chain 干净回合特异度门——prompt 显式带数据集+模型名,吃 chat.py 确定性强制注入(provider-stable),硬断言 = 链必跑 + H0 落 66.5-68.5 + 无 withheld/blocked/provenance-failed 横幅。F3_abstention_specificity (hard): 诚实弃答路径特异度门——纯乱码源名 "XQZW-J9999+9999" + 禁止替换对象,搜索确定性零命中→弃答卡;硬线 = 无横幅。**两个用例都活体验证**(deepseek 实跑):F3 v1 设计缺陷被活体抓出(源名含 "ALPINE" 令搜索命中真 ALPINE 论文、LFR seeding 拟合了整个真实样本)→ v2 重写后干净(1 工具 16s,模型还指出 +99° 赤纬超物理范围);F2 实跑暴露一次 cosmology_anchor 降级,**排查为按设计工作非误杀**(模型自加 "sits ~5σ below SH0ES H0=73.04" 段落,本轮无 shoes 数据集、73.04 无本轮来源——正是闸门文档明示要拦的类,A2 教正确路由),F2 留 soft 观测标记记录降级率。CLAUDE.md 计数与不变量段同步。验证: runner eval 10 绿 + 两用例活体硬门 0 失败 + benchmarks 22/22 + audit 净 + 全量 pytest 2350 绿。
- [x] **Union3 完整 22-bin binned 距离模数向量** (2026-06-12, commit 067472e): union3 从 1D 压缩 Ωm 高斯 (0.356±0.027) 升级为 cobaya sn.union3 同款完整 likelihood(同 lcparam_full.txt + mag_covmat.txt,CobayaSampler/sn_data,两文件 vendor+sha256 双钉);偏移解析边缘化 chi2 = δᵀC⁻¹δ−(ΣC⁻¹δ)²/ΣC⁻¹,与 cobaya 的 _marginalize_abs_mag 投影代数恒等(锁 ≤1e-8 平价测试);H0/M_B 边缘化掉,约束 Ωm(+w0/wa)。**常开、不设 env 开关**(des 的开关纯为 1829×1829 成本;22×22 没有这个理由)——chi2 最小值正好落在发表值 Ωm=0.3560,1σ 区间逐位吻合;wCDM w=−0.76 合 Rubin+2023。共享 _offset_marginalized_sn_chi2 核心 + per-key else-raise dispatch(含数据点计数、_entry_verification、audit、source records)。**对抗审查 24 agent/10 确认全处置**:① union3 强制 emcee 使 30 格矩阵 64s 超 45s 工具死线 → _sn_emcee_bypass_active 共享判定,矩阵(allow_emcee_fallback=False)走 importance(64s→25.7s,格子质量正常),贵向量(pantheon/des)永远 emcee;② artifact_sha256 十六进制数字漏进 claim universe(伪造 H0=64.3 通过)→ 黑名单补 sha256/artifact_sha256/runner_hash/data_hash/files_sha256 等 9 键 + 红队语料第 22 例;③ 诚实 "full likelihood" 措辞被 full_likelihood_overclaim 硬拦(F1 误杀类)→ 无压缩参与且聚合保真 full 的链打 claim_scope=executable_full_fidelity_likelihoods;④ 列名错位(预览把 zhel 当 mb 端给 LLM,sha256 光环下差 31.6 mag)→ columns 改全 5 列 + 预览断言;⑤ loader 失败记录被缓存毒化 → _load_union3_raw 失败即 raise(MGS 同款,有无 cache_clear 自愈测试);⑥ pairwise_tensions 静默丢 union3 行 → tension 集合并入保留发表锚点的已执行 SN 条目;⑦ 工具 schema 文案更新。**门控改动再过聚焦破坏性复核(3 agent)抓 3 个 major 再修**:假"full external Cobaya"声明被宽 scope 解锁 → 外部措辞(external/cobaya/cosmosis/desilike)仍要求真外部运行证据,in-process 解锁仅限朴素措辞;混合回合洗白(压缩链措辞蹭 union3 链豁免)→ 回合内有任何压缩 scope 链则不解锁(保守:混合回合诚实措辞也拦,换干净单链回合不误杀);headline warning 与新 scope 矛盾 → 同步改文案;前端补 full-fidelity 芯片。攻击样本全部活体复测:假外部拦/混合回合拦/desi 假外部拦/干净回合诚实放。已知残留(诚实记录):scope 仍按回合级证据,无句子↔链归属;des/pantheon loader 的失败记录缓存毒化为先例遗留(union3/MGS 已免疫)。验证: 定向 193+157 绿 + 前端 145 绿 + tsc build 净 + smoke 4/4 + benchmarks 22/22(union3 leg 改钉 full+sha)+ audit 29 净 + 全量 pytest 绿。
- [x] **SDSS MGS 非高斯 likelihood 升级** (2026-06-12, commit de43854): MGS 半边从手打高斯 (4.470±0.17) 升级为 cobaya bao.sdss_dr7_mgs 同款 399 点 chi2(alpha) 查表(同文件、同样条构造,数值平价 ≤1e-12 锁测试;alpha=(D_V/r_d)/4.29720761315,界 [0.8005,1.1985]);6dFGS 半边保持文献高斯。表 vendor+sha256 钉死,篡改响亮拒绝。**对抗审查 26 agent/22 条确认(去重后 4 major + 若干 minor)全部处置**:① audit_registry 对新表盲(篡改→audit 仍净)→ allowlist 改名 _MIXED_LITERATURE_PLUS_PINNED_OK 且 audit 要求钉死半边 hash_verified;② 链 provenance 报 sha256=None 低估保真 → load_verified_bao_data 增 sdss 分支带表摘要(等级仍 literature_typed,弱半边定级,诚实);③ 全样本出界时常数罚分在归一化重要性权重中相消→静默丢掉 MGS 约束 → 重要性采样器加 no-support 守卫响亮拒绝(blocked);④ 瞬态读失败被 lru_cache 缓存毒化进程 → loader 改为失败即 raise(异常不入缓存,自动恢复,有回归测试);minor:likelihood_family 改 bao_mixed_gaussian_table、行序断言(防 6dF/MGS 换位喂错表)、compressed_sources 增 sdss 记录、benchmark 改钉"chi2=6dF高斯+MGS查表"精确分解。判定为可接受不改:样条在表最小值下方轻微过冲(与 cobaya 构造逐位一致,平价即规格);chi2 表 399 个值经 load_cosmology_data_product 可进 claimable universe(与所有 vendored 表同一平台级 trade-off,值真实有源);execution_mode 字面量改名牵动 claim 门控,超本轮范围(注册表 notes 已澄清语义)。验证: 定向 100 绿 + smoke 4/4 + benchmarks 22/22(新分解断言)+ audit 净 + 全量 pytest 绿。
- [x] **注册表 external-only 巡查** (2026-06-12): 29 条全扫。结论:**vendor-2-files 级的便宜转换已清零**。逐类判定——4 条 Planck (plik_lite/lowl×2/lensing) 已是真 cobaya 执行体;planck_pr4_lensing/spt3g_cmb/act_dr6_lensing 需外部包(重,维持 pending,PR4 与 2018 lensing 互斥已设);6 条 H0/BBN 标量先验本来就是单数字,literature-typed 合法终态(6dF 同理:cobaya yaml 里就一个元组);WL 三条 = 红线;planck2018_compressed 的"完整版"即 plik_lite(已有)。真发现两条新保真项(MGS 非高斯 prob 分布、Union3 完整向量)已入 P2。另:盲测 06-11 班实为延迟 2.6h 后成功(非丢弃),修正前判;错峰 cron 仍是对症药。
- [x] **fit_line_lfr censoring/上限** (2026-06-12): opt-in `include_upper_limits`(默认关,全部既有基线不动)。物理纪律:真非探测没有线宽,x 绝不发明——只收 '<' 方向且表格真给 FWHM(+err) 的行(Kelly 2007 delta,仅贝叶斯;OLS 配上限/采样器失败都响亮拒绝)。**对抗审查抓 1 blocker + 2 major 全修**:censored 行原本跳过 L′ 单位转换与宇宙学重算(混参考系 likelihood,live-repro)→ 现走与探测完全相同的三段后处理(宇宙学/单位/透镜守卫);透镜上限绕过 mu 守卫 → 同门拦截;censored 行引用未入池 → citation_keys 并集。bare limit 的 ysig=中位探测误差(计数申报),零误差中位数响亮拒绝,<5 探测有专属 error_class。9 个回归测试。验证: benchmarks 22/22 + audit 净 + 全量 2323 绿。
- [x] **Planck 2018 lensing native 接入** (2026-06-12): CMBlikes native(smica consext8,9 bins)。vendor 最小集 1.3MB(5 文件 + 2 个窗口目录);窗口是 χ²-load-bearing(plik_lite bweight 教训),为此给 `_verify_pinned_cmb_data` 加了**目录聚合摘要**机制(sorted 文件名+字节,改/增/删/重命名任一窗口都翻红,机制本身有单测)。lensing 经 planck_calib 消费 A_planck → 进 CMB_APLANCK_KEYS;与 planck_pr4_lensing 互斥(同源 Planck 图)。锚点:−2lnL=8.82 / 9 bins(χ²/dof≈0.98,合发表值)。至此 clik-free Planck 2018 全套到齐:TT/TE/EE + lowE/lowT + lensing。验证: 56 目标测试 + smoke 4/4 + benchmarks 22/22 + audit 29 净 + 全量 2314 绿。
- [x] **ok_* 曲率模型在 CMB 路径** (2026-06-12): 双重实锤——`curved` 不是 CAMB 参数(CAMBUnknownArgumentError,所有 ok_* CMB 链在启动即死)且 omegak 从未进采样。修复 = 删伪参数 + omegak 进 CMB 参数序(先验 ±0.3)+ 把 w0→w 的别名机制泛化为 COBAYA_PARAM_ALIASES 表(omegak→omk)。物理判据:omegak=0 回 Λ 锚点 584.45,omegak=0.02 → 10992(声学峰对曲率极敏感),锁进回归测试。验证: smoke 4/4 + benchmarks 22/22 + audit 净 + 全量 2307 绿。
- [x] **mnu 在 cobaya CMB 路径缺失** (2026-06-12, commit 见下): 活体实锤——*_mnu 模型的 CMB 参数序无 mnu,链静默跑 CAMB 默认固定质量却标 mnu 模型名(比 w0 孤儿更险:静默冒名)。修复 = 参数序追加 mnu + CMB_PARAMETER_PRIORS 加 (0.0, 5.0) eV 平先验(放 CMB 表防 in-process 误拾)。物理判据:plik_lite 对 mnu 强响应(-2lnL 584→2044 @ 0.06→0.5 eV),已锁进回归测试。验证: smoke 4/4 + benchmarks 22/22 + audit 28/28 + 全量 2305 绿。
- [x] **Daily cron 失班处置** (2026-06-12): 11 日 16:00 UTC 班次被 GitHub 调度器丢弃(整点 cron 拥挤的已知行为,历史实跑已漂到 +2.4h);已手动补跑(run 27360552699,兼 F1 的 CI 首秀)并把 cron 错峰到 16:17 UTC(commit 8dc7987,**需推送生效**)。
