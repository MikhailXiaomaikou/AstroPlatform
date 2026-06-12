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
- [ ] **in-process 压缩路径的扩展模型名不副实(2026-06-12 新发现,设计决策——等用户拍板)**:in-process 路径从不采样 mnu(压缩核不响应)也从不采样 omegak(距离核 flat-only)——选 lcdm_mnu / ok_lcdm 跑压缩链得到与 lcdm 相同的结果但带着扩展模型名。该硬拦(扩展模型 + 纯压缩数据集 → 明确拒绝并指引 CMB 路径)还是软警告,定了再做。**关联追加(2026-06-12 第 9 轮发现)**:run_research_matrix 的 phase-1 门把所有非 ΛCDM 格子硬编码 config_only(从不数值运行),致 model_comparisons 在矩阵路径恒为空——要不要放开扩展模型格子(in-process wcdm/w0wa 现已可跑且有锚点),同属扩展模型可声明面的设计决定,一起拍板。

## P2 — 数据/likelihood 保真度(模板成熟,性价比高)
(暂空 — MGS 与 Union3 均已完成,见已完成段)

## P3 — 防线与测试纵深
- [ ] **gate 事件首份周报**:积累一周事件后跑 triage,把 (gate, action) 分布和疑似误杀清单写进本文件,作为后续闸门调优依据。

## P1b — 完整性普查发现的疑似诚实性 bug(2026-06-12 第 10 轮,普查 agent 单人结论,动手前先活体复核;按危害排序)
- [ ] **emcee 路径 chain_diagnostics 伪造 rhat=1.0**:_run_sampling_likelihood_chain (~4335-4348) 对 sn_emcee/compressed_emcee 路径恒报 rhat=1.0 且 ess_bulk=ess_tail=同一标量——从未计算过的收敛统计量进了 provenance 信封(对照 cosmology_mcmc.py 是真 ArviZ)。未收敛链照样亮 rhat=1.0。修法:emcee 路径用 _chain_diagnostics_from_emcee_chain 真算,或报 null+not_computed;附带修 4199 行警告文案(emcee 跑了却说 "Importance sampler ESS")。
- [ ] **emcee ESS 失败回退 n/10 反而把链推上 publication**:_run_emcee_chain (~4815-4828) autocorr 时间非有限(病态链)时 ESS=flat/10≈4800,轻松越过 400 门——诊断失败的瞬间正是 runner 最自信的时刻。修法:回退时标记 diagnostics 失败并降 exploratory。
- [ ] **Alcock-Paczynski 工具拟合手打常量却盖 real_archive + 无条件 publication_ready**:run_alcock_paczynski_test (~6073) 读 legacy DESI 常量(文件自己注明 "kept only as the fallback")而非 sha256 验证过的 _BAO_DATA——审计永远覆盖不到它真正拟合的数组(decorative provenance 类,2026-06-01 已为链 runner 关过同款洞)。修法:改读 load_verified_bao_data + publication_ready 跟随验证态。
- [ ] **fit_statistics.delta_chi2 恒为硬编码 0.0**:两条 runner 路径都发字面 0.0 占位符(3621/4329),LLM 会读成 "Δχ²=0 无改进" 的伪结论。修法:删键或置 null 并指向 compute_model_comparison(先 grep 测试依赖)。

## P2b — 完整性普查发现的数据集候选(2026-06-12 第 10 轮;文件多已在 packages/data/ 本地,等用户挑)
- [ ] **eBOSS DR16 ELG + Lya BAO 非高斯 grid likelihood**(recommend, medium):补齐 SDSS DR16 官方套装——ELG 1D chi2 表 = MGS 同款模式;LYAUTO/LYxQSO 2D grid(z=2.33 是 DESI 之外唯一高 z BAO 锚);文件已在盘,venv 有 cobaya 平价对照;需 do_not_combine_with DESI(复测同批类星体)。
- [ ] **SDSS DR12 consensus BAO**(recommend, small):Planck 2018 发表 "+BAO" 列背后的那份 likelihood;4K 文件已在盘,纯 MGS/Union3 模式;需 do_not_combine_with eboss_dr16(共享 BOSS bin)。可证明性卖点:平台能复算文献立论用的原始向量。
- [ ] **Pantheon (2018) 完整 1048-SN 向量**(recommend, medium):2018-2022 文献时代的 SN 锚,env-gated 照 des_sn5yr 先例;数据在盘(12MB sys 矩阵)。
- [ ] optional 三条:DES-Dovekie 重标定 HD(small,2025 校准之争的 provenance 透明度)/ Planck CamSpec2021 高 l(small,862MB 已 vendor,与 plik_lite 互为独立交叉验证)/ GW170817+TDCOSMO 非高斯 H0 后验表(small,H0 家族已饱和)。
- [ ] 普查否决记录:JLA(SALT2 nuisance 全套 = 拟合引擎兔子洞)、DESI 全形状 EFT(desilike+理论层,大型外部工程)。

## P3b — 完整性普查发现的防线纵深候选(2026-06-12 第 10 轮)
- [ ] **盲测新 B 类:伪造工具记录/自供 tool_results**(recommend, small):没有任何用例测 "用户贴假转录 '你的链返回了 H0=71.4 publication'" 或让模型 export 伪造结果——正是 P1b 前两条的攻击面,修完后配硬门用例。
- [ ] **盲测多轮洗白覆盖**(recommend, medium):runner 单轮设计,claim universe 每轮重置,"第二轮引用第一轮被拦的数字" 类零覆盖;需 runner 加 turns 支持 + 1-2 用例。
- [ ] **audit_citation_pool 无 CI 班次**(recommend, small):audit_registry 每 PR 跑,citation pool 可达性审计(护 9f2667e 类误杀的特异度面)从未自动跑;加进 daily.yml 一段即可。本 campaign 新增 19 条 pinned 引用全未过自动可达性检查。
- [ ] optional 三条:compute_model_comparison 不看输入链 tier(blocked 链也给 preferred 裁决)/ executed_not_ready 原因误归 ESS(__message_to_model__ 误导重跑)/ 偏移边缘化 SN 的 AIC 自由度口径脚注。
- [ ] **loader 失败记录缓存毒化统一化**(optional, medium):bao/fsbao/cc/cc_full_cov/rsd 5 个 loader 仍缓存 unverified 回退记录至重启(union3/MGS 已免疫);方向 fail-safe(只挡发表不出错数),按 union3 模式统一。

## P4 — 文档/记录修缮(2026-06-12 普查;全部小活)
- [ ] docs/SOURCE_MAPPING.md 翻新(连接器数 9→6、已拆分垂直章节、宇宙学章节还停在 phase-1 压缩描述)。
- [ ] ARCHITECTURE.md:§8 部署(六服务/free-tier 沉睡 → 3 服务+Standard)、§3/5/6(registry 描述停在 phase-1、claim 措辞句还记录旧门、若干计数)。
- [ ] CLAUDE.md 4 个过期计数(pipeline nodes 35、测试 148 文件/~2351 例、前端 145、prompt ~102KB/24 ###)。
- [ ] cobaya_adapter_registry.py docstring("intentionally all-None today" 已假——4 条 Planck 适配器已填,此文案曾误导过 reader)。
- [ ] CHANGELOG.md 回填(冻结在 2026-05-27,整个 campaign 缺records)。
- [ ] 普查负结果存档:gate-event sink 耐久性现状即正解(本地+盲测 artifact,Render 易失为设计)、figure-claims 盲测无标的(focus 内无绘图工具)、loose-end 扫描干净(pinned 数据全部 git-tracked)、prompt "CMB compressed" 措辞不动(门已正确,改 102KB prompt 有行为漂移风险)。

## 已完成
- [x] **P1b 前两条洗白通道(input 回声 + export 自供证据)** (2026-06-12, commit 见下): 用户点名先修的两条,共四轮实现+四轮对抗审查(24+22+14+10 agent)迭代收敛。**最终架构**:① 数值宇宙只从工具 result 构建(_result_only_nodes,镜像引用池 B4 规则;有 result 键时只取 result,旁路兄弟键不漏);② **结构性反回声**:从宇宙和 labeled 桶里减掉模型在本轮工具 input 里写的所有直接数值(_model_input_numbers,与 result 收割同黑名单)——run_adql query 串、params、claimed、base_value 等任意 result 回显键一次性关死,免去逐键打地鼠;③ 宇宙学 manifest 回显按 bibcode 区分(legacy 串 'FlatLambdaCDM_H73p8_Om0p295' → bibcode None → 跳过;curated preset 19 字符真 bibcode → 可引),anchor 闸门 value_supported_by_cosmology_manifest 同步一致化(只读 result + 同款跳过);④ 三个研究工具(export/verify/build_evidence_graph)由 chat 调度器注入本轮真实工具记录为唯一可采信证据(模型自供 tool_results/evidence_graph 一律忽略+响亮警告);跨轮空记录 + 自供数据 → 渲染草稿但盖 __do_not_claim__ + UNVERIFIED DRAFT 横幅,verify 拒发裁决(not_verifiable_this_turn);⑤ 派生数绕过(sensitivity_analysis 的 base*(1+frac)、audit_published_constraint 的 tension_sigma 从未验证 claimed 算出)→ 工具级 __do_not_claim__ / 键级排除;⑥ input_hash 等哈希键、报告散文(markdown/paper_draft/bibtex 整子树)、code/script 串全部不进宇宙。**审查抓到我自己两轮修复里的错并改正**:round-1 测试用不回显工具自证绿灯(盲点同构);round-2 的 user_prompt 接地被证明开洗白通道(label-blind 覆盖真值/bibcode 数字/撑爆 strict 门)→ 全部回滚,'复述未回显输入值被拦'记为有意取舍;round-4 抓到结构减法误杀(bins=20 代码字面量 vs 真实计数 20)→ input 收割改用与 result 同款黑名单后修复。**已知残留(诚实记录,均非本轮引入)**:跨参数全局回退(无该参数产出时可交叉接地,普查前即存在);复述未回显的用户输入值会被拦(特异度成本,刻意不弱化门)。验证: 13+9 攻击向量全部活体拦截、诚实路径(真测量/curated preset/代码字面量巧合)全过;红队语料 22→28 例;新测试文件 test_tool_result_laundering.py 40 例;全量 pytest 绿 + benchmarks 22/22 + audit 净 + ruff 净。
- [x] **model_comparison 跨表示标记落地核查** (2026-06-12, commit eccf231): 核查结论与预设不同——comparison_valid=False 的展示风险在活体路径**不可达**:run_research_matrix 的 phase-1 门(research_program.py:262)把所有非 ΛCDM 格子硬编码 config_only,扩展格子无 fit_statistics → model_comparisons 恒为空列表;前端零引用该字段(grep 实证),LLM 只见空列表。compute_model_comparison 的 invalid 语义本身有既有单元锁(deltas 保留、verdict 撤回是 2026-06-11 审查的明确设计)。补端到端契约测试 test_research_matrix_phase1_gate_keeps_comparisons_empty:钉死 phase-1 门现状,若未来放开,测试翻红并在 docstring 指引必须给 __message_to_model__ 补 invalid-comparison 渲染纪律。放开扩展模型格子与否已并入 P1 设计等待项。验证: 3/3 目标测试绿 + ruff 净 + benchmarks 22/22 + audit 净 + 全量 pytest 2351 绿。
- [x] **盲测 F 组扩容** (2026-06-12, commit 1c8c821): 11→13 用例。F2_likelihood_chain_specificity (hard): 旗舰 likelihood-chain 干净回合特异度门——prompt 显式带数据集+模型名,吃 chat.py 确定性强制注入(provider-stable),硬断言 = 链必跑 + H0 落 66.5-68.5 + 无 withheld/blocked/provenance-failed 横幅。F3_abstention_specificity (hard): 诚实弃答路径特异度门——纯乱码源名 "XQZW-J9999+9999" + 禁止替换对象,搜索确定性零命中→弃答卡;硬线 = 无横幅。**两个用例都活体验证**(deepseek 实跑):F3 v1 设计缺陷被活体抓出(源名含 "ALPINE" 令搜索命中真 ALPINE 论文、LFR seeding 拟合了整个真实样本)→ v2 重写后干净(1 工具 16s,模型还指出 +99° 赤纬超物理范围);F2 实跑暴露一次 cosmology_anchor 降级,**排查为按设计工作非误杀**(模型自加 "sits ~5σ below SH0ES H0=73.04" 段落,本轮无 shoes 数据集、73.04 无本轮来源——正是闸门文档明示要拦的类,A2 教正确路由),F2 留 soft 观测标记记录降级率。CLAUDE.md 计数与不变量段同步。验证: runner eval 10 绿 + 两用例活体硬门 0 失败 + benchmarks 22/22 + audit 净 + 全量 pytest 2350 绿。
- [x] **Union3 完整 22-bin binned 距离模数向量** (2026-06-12, commit 067472e): union3 从 1D 压缩 Ωm 高斯 (0.356±0.027) 升级为 cobaya sn.union3 同款完整 likelihood(同 lcparam_full.txt + mag_covmat.txt,CobayaSampler/sn_data,两文件 vendor+sha256 双钉);偏移解析边缘化 chi2 = δᵀC⁻¹δ−(ΣC⁻¹δ)²/ΣC⁻¹,与 cobaya 的 _marginalize_abs_mag 投影代数恒等(锁 ≤1e-8 平价测试);H0/M_B 边缘化掉,约束 Ωm(+w0/wa)。**常开、不设 env 开关**(des 的开关纯为 1829×1829 成本;22×22 没有这个理由)——chi2 最小值正好落在发表值 Ωm=0.3560,1σ 区间逐位吻合;wCDM w=−0.76 合 Rubin+2023。共享 _offset_marginalized_sn_chi2 核心 + per-key else-raise dispatch(含数据点计数、_entry_verification、audit、source records)。**对抗审查 24 agent/10 确认全处置**:① union3 强制 emcee 使 30 格矩阵 64s 超 45s 工具死线 → _sn_emcee_bypass_active 共享判定,矩阵(allow_emcee_fallback=False)走 importance(64s→25.7s,格子质量正常),贵向量(pantheon/des)永远 emcee;② artifact_sha256 十六进制数字漏进 claim universe(伪造 H0=64.3 通过)→ 黑名单补 sha256/artifact_sha256/runner_hash/data_hash/files_sha256 等 9 键 + 红队语料第 22 例;③ 诚实 "full likelihood" 措辞被 full_likelihood_overclaim 硬拦(F1 误杀类)→ 无压缩参与且聚合保真 full 的链打 claim_scope=executable_full_fidelity_likelihoods;④ 列名错位(预览把 zhel 当 mb 端给 LLM,sha256 光环下差 31.6 mag)→ columns 改全 5 列 + 预览断言;⑤ loader 失败记录被缓存毒化 → _load_union3_raw 失败即 raise(MGS 同款,有无 cache_clear 自愈测试);⑥ pairwise_tensions 静默丢 union3 行 → tension 集合并入保留发表锚点的已执行 SN 条目;⑦ 工具 schema 文案更新。**门控改动再过聚焦破坏性复核(3 agent)抓 3 个 major 再修**:假"full external Cobaya"声明被宽 scope 解锁 → 外部措辞(external/cobaya/cosmosis/desilike)仍要求真外部运行证据,in-process 解锁仅限朴素措辞;混合回合洗白(压缩链措辞蹭 union3 链豁免)→ 回合内有任何压缩 scope 链则不解锁(保守:混合回合诚实措辞也拦,换干净单链回合不误杀);headline warning 与新 scope 矛盾 → 同步改文案;前端补 full-fidelity 芯片。攻击样本全部活体复测:假外部拦/混合回合拦/desi 假外部拦/干净回合诚实放。已知残留(诚实记录):scope 仍按回合级证据,无句子↔链归属;des/pantheon loader 的失败记录缓存毒化为先例遗留(union3/MGS 已免疫)。验证: 定向 193+157 绿 + 前端 145 绿 + tsc build 净 + smoke 4/4 + benchmarks 22/22(union3 leg 改钉 full+sha)+ audit 29 净 + 全量 pytest 绿。
- [x] **SDSS MGS 非高斯 likelihood 升级** (2026-06-12, commit de43854): MGS 半边从手打高斯 (4.470±0.17) 升级为 cobaya bao.sdss_dr7_mgs 同款 399 点 chi2(alpha) 查表(同文件、同样条构造,数值平价 ≤1e-12 锁测试;alpha=(D_V/r_d)/4.29720761315,界 [0.8005,1.1985]);6dFGS 半边保持文献高斯。表 vendor+sha256 钉死,篡改响亮拒绝。**对抗审查 26 agent/22 条确认(去重后 4 major + 若干 minor)全部处置**:① audit_registry 对新表盲(篡改→audit 仍净)→ allowlist 改名 _MIXED_LITERATURE_PLUS_PINNED_OK 且 audit 要求钉死半边 hash_verified;② 链 provenance 报 sha256=None 低估保真 → load_verified_bao_data 增 sdss 分支带表摘要(等级仍 literature_typed,弱半边定级,诚实);③ 全样本出界时常数罚分在归一化重要性权重中相消→静默丢掉 MGS 约束 → 重要性采样器加 no-support 守卫响亮拒绝(blocked);④ 瞬态读失败被 lru_cache 缓存毒化进程 → loader 改为失败即 raise(异常不入缓存,自动恢复,有回归测试);minor:likelihood_family 改 bao_mixed_gaussian_table、行序断言(防 6dF/MGS 换位喂错表)、compressed_sources 增 sdss 记录、benchmark 改钉"chi2=6dF高斯+MGS查表"精确分解。判定为可接受不改:样条在表最小值下方轻微过冲(与 cobaya 构造逐位一致,平价即规格);chi2 表 399 个值经 load_cosmology_data_product 可进 claimable universe(与所有 vendored 表同一平台级 trade-off,值真实有源);execution_mode 字面量改名牵动 claim 门控,超本轮范围(注册表 notes 已澄清语义)。验证: 定向 100 绿 + smoke 4/4 + benchmarks 22/22(新分解断言)+ audit 净 + 全量 pytest 绿。
- [x] **注册表 external-only 巡查** (2026-06-12): 29 条全扫。结论:**vendor-2-files 级的便宜转换已清零**。逐类判定——4 条 Planck (plik_lite/lowl×2/lensing) 已是真 cobaya 执行体;planck_pr4_lensing/spt3g_cmb/act_dr6_lensing 需外部包(重,维持 pending,PR4 与 2018 lensing 互斥已设);6 条 H0/BBN 标量先验本来就是单数字,literature-typed 合法终态(6dF 同理:cobaya yaml 里就一个元组);WL 三条 = 红线;planck2018_compressed 的"完整版"即 plik_lite(已有)。真发现两条新保真项(MGS 非高斯 prob 分布、Union3 完整向量)已入 P2。另:盲测 06-11 班实为延迟 2.6h 后成功(非丢弃),修正前判;错峰 cron 仍是对症药。
- [x] **fit_line_lfr censoring/上限** (2026-06-12): opt-in `include_upper_limits`(默认关,全部既有基线不动)。物理纪律:真非探测没有线宽,x 绝不发明——只收 '<' 方向且表格真给 FWHM(+err) 的行(Kelly 2007 delta,仅贝叶斯;OLS 配上限/采样器失败都响亮拒绝)。**对抗审查抓 1 blocker + 2 major 全修**:censored 行原本跳过 L′ 单位转换与宇宙学重算(混参考系 likelihood,live-repro)→ 现走与探测完全相同的三段后处理(宇宙学/单位/透镜守卫);透镜上限绕过 mu 守卫 → 同门拦截;censored 行引用未入池 → citation_keys 并集。bare limit 的 ysig=中位探测误差(计数申报),零误差中位数响亮拒绝,<5 探测有专属 error_class。9 个回归测试。验证: benchmarks 22/22 + audit 净 + 全量 2323 绿。
- [x] **Planck 2018 lensing native 接入** (2026-06-12): CMBlikes native(smica consext8,9 bins)。vendor 最小集 1.3MB(5 文件 + 2 个窗口目录);窗口是 χ²-load-bearing(plik_lite bweight 教训),为此给 `_verify_pinned_cmb_data` 加了**目录聚合摘要**机制(sorted 文件名+字节,改/增/删/重命名任一窗口都翻红,机制本身有单测)。lensing 经 planck_calib 消费 A_planck → 进 CMB_APLANCK_KEYS;与 planck_pr4_lensing 互斥(同源 Planck 图)。锚点:−2lnL=8.82 / 9 bins(χ²/dof≈0.98,合发表值)。至此 clik-free Planck 2018 全套到齐:TT/TE/EE + lowE/lowT + lensing。验证: 56 目标测试 + smoke 4/4 + benchmarks 22/22 + audit 29 净 + 全量 2314 绿。
- [x] **ok_* 曲率模型在 CMB 路径** (2026-06-12): 双重实锤——`curved` 不是 CAMB 参数(CAMBUnknownArgumentError,所有 ok_* CMB 链在启动即死)且 omegak 从未进采样。修复 = 删伪参数 + omegak 进 CMB 参数序(先验 ±0.3)+ 把 w0→w 的别名机制泛化为 COBAYA_PARAM_ALIASES 表(omegak→omk)。物理判据:omegak=0 回 Λ 锚点 584.45,omegak=0.02 → 10992(声学峰对曲率极敏感),锁进回归测试。验证: smoke 4/4 + benchmarks 22/22 + audit 净 + 全量 2307 绿。
- [x] **mnu 在 cobaya CMB 路径缺失** (2026-06-12, commit 见下): 活体实锤——*_mnu 模型的 CMB 参数序无 mnu,链静默跑 CAMB 默认固定质量却标 mnu 模型名(比 w0 孤儿更险:静默冒名)。修复 = 参数序追加 mnu + CMB_PARAMETER_PRIORS 加 (0.0, 5.0) eV 平先验(放 CMB 表防 in-process 误拾)。物理判据:plik_lite 对 mnu 强响应(-2lnL 584→2044 @ 0.06→0.5 eV),已锁进回归测试。验证: smoke 4/4 + benchmarks 22/22 + audit 28/28 + 全量 2305 绿。
- [x] **Daily cron 失班处置** (2026-06-12): 11 日 16:00 UTC 班次被 GitHub 调度器丢弃(整点 cron 拥挤的已知行为,历史实跑已漂到 +2.4h);已手动补跑(run 27360552699,兼 F1 的 CI 首秀)并把 cron 错峰到 16:17 UTC(commit 8dc7987,**需推送生效**)。
