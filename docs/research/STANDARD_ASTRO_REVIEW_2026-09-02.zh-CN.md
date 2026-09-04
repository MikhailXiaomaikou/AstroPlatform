# Standard Astro 全面审查 × 讨论笔记：优化方案

- **审查日期**：2026-09-02
- **审查对象**：你的《Standard-Astro 讨论有效信息整理》（下称"笔记"）对照主仓库 `~/Projects/astro-platform` main@3a7e6e4（2026-08-11 之后零提交）。
- **审查方式**：只读，不改仓库。10 路读代码建图 → 6 个视角的批评者 → 28 条合并发现，每条由 3 个独立反驳者（代码证据 / 是否违反项目红线 / 可行性）试图推翻，2/3 存活才保留 → 完整性检查补 6 条。最后我亲自复核了最重的 8 条（见附录）。每条结论带 `路径:行号` 或 commit；查不到的明写"未核实"。
- **工时口径**：全部按 Claude Code 代理工时（并行工具、无切换成本），不是人类工程师口径。

---

## 十二行白话总结

1. **笔记诊断的症状是真的，病因归错了。**"模型不敢深究、总差一口气"在代码里有真实机制，但不是"人类复核"造成的：提示词语料里"human"这个词出现 0 次，聊天路径没有任何审批步骤。真正的限制器是三样东西：一个每轮把工具菜单缩到 1 个甚至 0 个、还替模型做决定的确定性"导演层"（loop.py），一份 106 KB、约 100 条"禁止"、零条"请给出替代解释"的提示词，以及二十来道只认"数字是否来自本轮工具"的出口闸门。
2. **比你说的更糟：聊天路径上模型看不见 60 个工具里的 39 个。**chat.py 按"专家分类"过滤工具，而五个专家的名单里没有任何一个宇宙学或研究类工具。build/run_cosmology_*、fit_cosmology_mcmc、plan_research_program 这些招牌工具从不出现在模型看到的 schema 里，只能靠正则强塞。而 90.4% 这个成绩是在盲测跑道上测的，那条跑道把全部工具都给了模型，真实用户从没拿到过。
3. 打个比方：你以为是裁判太严让球员不敢射门。实际是教练每回合只发一个球给他，经常自己上场替他踢，赛后还把他的赛后感言改成模板。裁判（闸门）确实也有几处误判，但改裁判规则不是第一步。
4. **两台仪表盘黑了三到六周，没人发现。**每日盲测 08-11 起连红 22 次（DeepSeek 返回 400，要求回传 `reasoning_content`），每周科学回归 07-26 起连红 6 次（CI 的 checkout 少一行 fetch-depth）。README 和 HONESTY_EVIDENCE 却还写着"每天硬拦、最近 15 次 8 次绿"。一个卖"不造假"的项目，正在对自己的状态说过时的话。
5. **合并后的 HEAD 从没跑过复跑基线。**08-11 唯一两次尝试有一半样本是传输失败（claude CLI 在 Claude Code 会话里跑退出码 1、kimi 撞 argv 上限、codex 不在 PATH）。你自己 08-06 的演示脚本第 6 行写着"HEAD 复跑前禁止对外演示"，这条至今没满足。
6. **笔记的 P0–P6 七条，没有一条写了"谁需要 / 怎么算过 / 多少工时"**，全是内部能力项。这正是 08-09 复盘点名的"完美主义回路"（审查到第 62 轮、beta 帖一个字没发）换了件衣服。笔记还顺手把 06-03 定下的"只做观测宇宙学"翻掉了，没给理由。
7. **笔记里 P1（科研环境）、P4（记忆）、P6（多智能体）、§5.2（模型造工具）在代码树里都已经存在。**要么暗启动关着（Foundry 六个开关、Workspace 1,521 行、Evidence Pack v2），要么建了又当天回滚（07-01 记忆功能），要么活着但是个门面（五专家顺序串跑、400 字截断交接）。你在按"名字"规划，不是按"库存"规划。
8. **笔记的排序倒了。**P0（大胆探索）没有安全的输出通道：探索出来的数字要么被闸门删掉，要么先于闸门泄漏在思考流里（07-22 就立案的漏洞，至今未改）。P3（结论分级）代码骨架已建七成，最便宜；P5（报告）是博士后唯一能拿在手里的东西。这两个被你排在三个架构重写后面。
9. **正确顺序**：修仪器 → P3-lite（三处提示词/闸门矛盾 + 泄漏）→ P5 报告脚手架 → **先测**"总差一口气"是否可复现 → 复现了才写探索窗口代码 → 其余进候选池。
10. **博士后 Demo 的高潮要换。**别拿"模型独立推进研究得出 Findings"当高潮：平台最强的路径按设计以 WITHHELD 收尾，而且研究链现在的最终回复是一句话拒答（原因是 honesty.py 把摘要里的 "0 ready out of 7" 当成泄漏的后验数字误杀了）。把高潮改成"博士后当场核一个他在意的数字"，这才对得上你 08-09 自己写的价值修正："替你核别人的数字，比替你算值钱"。
11. **好消息**：上面 80% 的修复项都在 15 分钟到 2 小时代理工时之间，不动任何闸门阈值；四个绿了 40 天的 PR 只等你四次点击。
12. **坏消息**：仪器修好、HEAD 基线跑出来之前，笔记里任何一条 P 都不该开工，任何一个数字都不该对外引用。

---

## 一、对这份笔记的总体判决

### 1.1 笔记说对了什么

| 笔记论点 | 判决 | 证据 |
|---|---|---|
| §2.2 现有实现让模型"过早自我约束" | **机制层面对**。loop.py 每轮 20 处 `visible_tools` 重赋值只减不增，多处缩到 1 个或空列表；6 个分支跳过 LLM 直接合成 `tool_use`；10 处丢弃模型自己的选择；研究/宇宙学流程里模型中间文本被扣住、最终文本被模板覆盖 | loop.py:708-1034、:1408-1532、:1583-1673、:1782-1800、:3462-3483 |
| §2.2 "免责声明驱动" | **对，且有一处铁证**。cosmology/prompt.md 教模型"可以带 exploratory 前缀引用探索链后验、可以说 X–Y 范围"；honesty.py 的终稿门对任何落在被扣后验 ±1% 内的数字整篇替换（注释原话："even with an exploratory caveat"）。提示词 05-21 写的，闸门 07-17 加的，从未对齐。**模型被提示词训练着往墙上撞** | prompt.md:98-109、:413-425；honesty.py:197-236；loop.py:3764-3790 |
| §2.2 提示词压制探索 | **对**。约 100 条 never / do not / must not，零条"给出替代解释 / 多个假设"；"hypothesis" 在 prompt.md 里只出现为贬义（"a single chain is a hypothesis, not evidence"）和路由触发词；诚实拒答被定义为"成功" | base.md:417-443；prompt.md:90 |
| §2.1 "模型感知到的是工具菜单" | **对，且比你说的更糟**：聊天路径上 39/60 个工具根本不在模型看到的 schema 里（见 H4） | chat.py:640-646、:690-694 |
| §3 阶段 C 需要结论分级 | **对**。分级只存在于工具结果层（publication_ready / chain_tier / __do_not_claim__ / response_disposition），不存在于回复里的单条 claim；Claim / ValidationResult 没有 tier 字段 | claim_validator.py:502-520；loop.py:196-290 |
| §9 多智能体不是当前优先 | **结论对，理由错**。现存的五专家串跑是个有害门面（见 H4），应删不应长 | orchestrator.py:32-61、:189-229；chat.py:1544-1608 |
| §10 Demo 要一个完整闭环而非零散能力 | **对**。现有 22 分钟脚本 5 个提示里 4 个是毫秒级确定性路由，没有一个跑链、拟合或报告 | 演示脚本 :84-111；战役报告 :65 |
| §11 P5 报告有价值 | **对，但排错位**。后端已能吐 13 个标题的报告，前端把它塞在折叠面板里的 0.72rem `<pre>` | research_program.py:1053-1160；ResearchProgramPanel.tsx:801-815 |
| §6 框架应与模型无关 | 与北极星一致，但不是新决定 | CLAUDE.md:12-15 |

### 1.2 笔记说错了什么

| 笔记论点 | 判决 | 证据 |
|---|---|---|
| §2.2 限制器是"人类复核机制" | **错**。提示词语料 0 次出现 "human"；聊天路径没有任何审批状态、队列或人类步骤。限制器 = 确定性导演层 + 提示词禁令 + 出口闸门。改"人类复核的位置"改不掉这三样 | `rg human backend/app/prompts` → 0；loop.py:3900-3935 |
| §9 "当前仍是单 Agent" | **产品层面可以这么说，代码层面错**。五专家正则编排器就在生产路径上：复合提示触发最多 3 个专家各 360 秒顺序串跑，400 字截断交接，字符串拼接合并；把它折叠掉的修复藏在默认 False 的开关后面。ARCHITECTURE.md:627 同样说错 | orchestrator.py:74-90、:167-171、:189-197、:220-229；config.py:229 |
| §2.1 "注册表是瓶颈" | **错**。注册表是 provenance 边界：数字只有从固定工具边界收割才可声明，这是全项目唯一护城河。僵化住在 3,630 行 prompt_routing 加 loop.py 导演层，它 05-28 建起来是为了压过 DeepSeek/Anthropic 排序器的第一选择。**模型越强越被它盖掉** | CLAUDE.md:159-165；prompt_routing.py:3285-3295；loop.py:1678-1681；blind_test README.md:37-52 |
| §2.1 "工具数量增加后维护成本高" | **前提错**。注册表在缩（81 实现、20 保留不上表、18 个无 schema 的死分支、06-03 拆走两条垂直）。真成本是每个工具名散在约 7 张表 + loop 门 + claim_validator + 提示词 + 测试里（fit_line_lfr 一个工具牵 30 个后端文件、32 个测试文件）。数量不是问题，耦合是，但没人量过 | ai_tools/__init__.py:477-585；git 7872bd8 |
| §1/§12 扩展到"通用科研 Agent 框架" | **翻了三个决定不给理由**：CLAUDE.md:8,11 只做宇宙学；06-03 把垂直拆出去（理由：北极星聚焦、给评审者一个干净仓库）。笔记承认收窄过，所以不算"偷偷"，但没答"当初的理由为什么不再成立"，也没为更大范围点名任何真实用户。这是 03-16 "全栈 SaaS"模式的重演，当年留下的 25–33% 非核心代码今天还在 | CLAUDE.md:8,11,17-19；技术报告 L80 |
| §4 / §5.2 / §8 / §9 当作新方向提出 | **错，树里都有**：memory_service（308 行，past_hypotheses 列从未写入）、07-01 记忆功能当天回滚（3a60b0b → cd3ff5e）、research_workspace_service 1,521 行（只认 arXiv 2311.12098 一篇，四张表装不下数据/代码/笔记）、Foundry 六个开关（AI 起草 → 沙箱 → 人审，foundry_generated/ 目录为空）、evidence_pack_v2、workflow_registry_v2。约 24k 行开关关着的后端代码 | memory_service.py:149,303；research_workspace_service.py:24-40、:753-1143；config.py:221-244 |
| §11 排序 P0 → P6 | **倒了**。P0 无安全输出通道，P3 已建七成，P5 是唯一 Demo 可消费项 | 见第三章 |
| 全文 | **零引用**。来源写"本次对话"，全文没有一个文件路径、案例 id、PR 号或百分比；不提目标用户；不提 08-09 的价值修正（"替你算"是弱牌，"替你核别人的数字"才值钱），P0/P2/§7 却是"替你算 / 替你研究"的最大化版本 | 笔记 L3；技术报告 3.2.6 |

### 1.3 笔记没测过的东西

| 论断 | 现状 |
|---|---|
| "总差一口气 / 过早停止 / 假设推得不够深" | **零案例 id、零转录、零指标**。v0.2 六维评分没有深度维度（score_standard_astro_v02.py:37-44）；盲测 runner 记了 hit_iteration_cap / hit_deadline 但没有任何检查读它们（runner.py:176-179,197-200 对比 :558-642）；8 个冻结任务里 4 个按设计奖励"停下"。仓库里最接近的定量痕迹是 8/120 处置不匹配（V02_03 6/15、V02_04 2/15），且是**闸门处置行为**（期望 full 给了 limited / hard_block），不是模型胆小。EDE 任务 V02_07 平台 100% vs 裸模型 75%，模型参与度 0 |
| "环境化比加工具 / 加上下文更能解决一口气" | 三个未测选项之间的比较 |
| §6 "框架驾驭更强模型" | **当前回路下不可测**：导演层专为压过强模型的第一选择而建；Daily 只跑 DeepSeek；90.4% 来自 gpt-5.6-sol/terra/luna、claude-fable-5、kimi-k3，**没有一个是托管默认模型** |
| §2.1 维护成本 | 无 LOC/工具 数据 |
| 你抱怨的那些会话是在哪个开关状态下跑的 | 未核实。评测 runner 强开 LIGHTWEIGHT_VERIFICATION_ENABLED=1，生产默认 False，演示脚本又开它。这个开关决定 "explore/探索/假设" 这些词是否被路由到一条**剥掉重型工具**的车道（见 H6b） |

---

## 二、代码库现状硬伤（按严重度排序）

| # | 硬伤 | 严重度 | 证据 | 修复工时 |
|---|---|---|---|---|
| H1 | **Daily 盲测连红 22 次**（08-11 → 09-01，全在 3a7e6e4；上次绿 08-07）。18 例中 8 例在自动注入工具路由后的第一次 DeepSeek 调用收到 400 "reasoning_content must be passed back"，回落到带标注的确定性摘要，F2 硬门每天挂。无 issue、无分诊。根因两个候选：① e58d79e 把自动路由从"LLM 后覆盖"改成"LLM 前合成响应"（loop.py:1448-1495），合成的 assistant 轮没有 reasoning_content，而 Daily 用 deepseek:v4-pro 开思考模式；② DeepSeek 合约外部变更。回传代码本身 04-26 就在、与 08-07 绿跑逐字节相同。**需一次真实调用复现** | P0（仪器） | `gh run list --workflow=daily.yml`；run 33549332597 日志；loop.py:1536-1560；inference_router.py:162-211；model_profiles.py:111；runner.py:136 | 1–2 h |
| H2 | **Weekly Scientific Validation 连红 6 周**（07-26 → 08-30）：scientific-validation.yml 两处 checkout 无 fetch-depth，浅克隆下 `git merge-base --is-ancestor` 返回 128，一个测试挂掉整个 job（963 通过 1 失败）。ci.yml 同一测试设了 fetch-depth: 0 所以 PR CI 绿、漂移没人看见。没有任何文档提过这个 workflow | P1（仪器） | scientific-validation.yml:27,152 对比 ci.yml:17-19；test_w0wa_exact_pipeline.py:1746-1752；run 33332140929 | 10 min + 47 min CI |
| H3 | **HEAD 无复跑基线；CLI 桥断；源解析冷缓存超时**。复跑输出目录不存在；08-11 两次复跑 24/48、22/48 传输失败：claude --print 退出 1 且 stderr 为空（与"在 Claude Code 会话内跑"一致）；kimi 12/12 撞 argv 上限（#53）；codex 不在 PATH。确定性层掉到 87.5%，因为 V02_01/02 全部 source_status=unavailable、error_class=source_budget_exhausted：30 秒总预算冷缓存超时。08-06 的 100% 全靠缓存命中，e58d79e 新加的钉 IP 抓取路径从未被证明能用 | P0（仪器） | `.local/standard-astro-v02-natural` 不存在；v02 副本 rerun_2026-08-11{,b}_summary.json；source_packet_resolver.py:54、:340-403；README.md:77-79 | 诊断 45–90 min；复跑 1–4 h 无人值守 |
| H4 | **聊天路径藏掉 39/60 个工具**。`_filter_tools` 取正则分类专家的 tool_names 并集；无专家含任何 cosmology / research 工具；宇宙学提示只命中 data_agent（9 个）或 analysis_agent（18 个）。61 个允许工具里 40 个不在任何专家名单（我用 venv 内省实测）。fit_cosmology_mcmc、run_nested_sampler、compute_theory_cmb_spectrum、evaluate_chain_diagnostics 等没有强塞触发词的工具，聊天里**任何路径都到不了**。盲测 runner 绕过编排器把全部工具给回路（runner.py:132-157）：**90.4% 是在用户从未拿到的工具集上测的** | P0 | chat.py:640-646、:690-694、:1544-1608；orchestrator.py:74-90、:167-171；`rg 'cosmology\|mcmc\|research' backend/app/ai/agents/` → 0 | 30 min（开关版）；2–4 h（删除串跑版） |
| H5 | **闸门前泄漏（07-22 审计项，未改）**。每个无工具调用的迭代（含最终稿）原文以 agent_text SSE 先于全部闸门发出；"Draft intermediate prose withheld" 占位只在有工具调用且是研究/宇宙学流程时触发；前端当 thinking 渲染，回复落地后丢弃，但**已上线、已持久化到 audit_trail**。实测：B2 07-23 泄出探索后验 67.32±0.60 和不可信用户值 71.4；F2 07-25 泄出整段链结果。任何"探索阶段"都会放大这个通道 | P0 | loop.py:1780-1800；client.ts:3087-3096；ChatMessageList.tsx:72-83；chat.py:1038 | 45–90 min |
| H6a | **提示词命令模型产出闸门必删的东西**。prompt.md:413-425 "You MAY discuss the posterior median… prefix with exploratory / X–Y range"；honesty.py rel_tol=0.01 无措辞豁免；任何由后验构造的区间端点必命中。item 4 还让模型输出 `__exploratory_warning__` 字面量，被 A1 盲测禁止。两个 runner 早已在工具边界告诉模型扣数，系统提示词是矛盾的最后来源 | P1 | prompt.md:98-109、:413-425；honesty.py:197-236；loop.py:3764-3790；cases.yaml:63 | 15–30 min + 1 回归测试 |
| H6b | **"探索"这个词会让模型拿到更少的工具**。prompt_routing.py 把含 research / study / explore / investigate / hypothesis / 研究 / 探索 / 假设 的提示分类为 research_exploration，heavy_route_allowed=False，剥掉 11 个重型工具。仅在 LIGHTWEIGHT_VERIFICATION_ENABLED=1 时生效（演示栈开着它） | P1 | prompt_routing.py:1197-1207、:1227；loop.py:615-620、:721-741 | 并入阶段 4 |
| H7 | **honesty.py 把整数计数当泄漏后验，杀掉研究链的最终回复**。研究模式摘要对非 ready 单元不打印中位数，E1 08-06 摘要里唯一的数字是 "0 ready out of 7"、"0 verified, 1 removed"；honesty 遍历器把任何叫 `parameters` 的子字典当后验，从 prior_dominance_screen.parameters 收到 0.0 / 1.0，与 "0"/"1" 在容差内相撞 → 整篇替换为一句话拒答。80 秒、6 次工具调用归零。08-06 summary.md 把 E1 归为 model_drift，**错分类**：这是确定性产品缺陷 | P1 | summaries.py:1062-1101；honesty.py:48-60、:197-234；v02 results_20260806_163214/case_E1 | 约 10 行 + 测试，30–45 min；**方向需你拍板** |
| H8 | **honesty.py 分词器四个绕过 + 68% 误杀**（07-17 起逐字节未动）：`_NUMBER_RE` 尾部 lookahead 丢掉带单位/指数的数（'H0 = 73.2km/s/Mpc' → 无 token）、无视 h 单位、拆开 '7.32×10^1'、不认拼写数字；无百分号豁免（'The 68% interval' vs 探索中位 68.3 → 命中）。F2 合约下它是唯一防线 | P1 | honesty.py:19-22、:224-236；git d24b05c | 1–2 h + 6 测试；'%' 豁免**要你签字** |
| H9 | **裸词 "hypothesis / forecast" 洗白断言结论**。`_NONASSERTIVE_COSMOLOGY_CONTEXT_RE` 含裸 `hypothesis\|forecast`，"Our hypothesis is confirmed: the Hubble tension is resolved by a local void." 零违规通过，去掉这个词就被拦；中文 `假设\|预测` 同洞。今天就活着 | P1 | claim_validator.py:3603-3630（:3622、:3628-3630）；test_claim_validator.py:718-742 | 20–30 min |
| H10 | **公开状态文案过时**。README "hard-blocked in CI every day"；HONESTY_EVIDENCE "as of 2026-07-10 … 8 of last 15 scheduled runs green"。一个照页面建议去看 Actions 标签的博士后会看到 28 连红。能让它机器生成的 PR #35 开着 40 天 | P1 | README.md:65-67；HONESTY_EVIDENCE.md:10、:82-88 | 15 min 文案；#35 rebase 30 min |
| H11 | **90.4% 无任务范围说明**。60 个模型在环样本全部来自 V02_03–06；V02_01/02/07/08 是 100% 流水线（llm_calls=0）；"0/15 伪数复述"来自 V02_08，也是纯流水线。README 与演示脚本数字卡都没写 | P1 | postfix_summary.json；README.md:71-72；演示脚本 :10-25 | 15–20 min |
| H12 | **served 模型 ≠ 评测模型**。托管唯一付费模型 DeepSeek，Daily 自动选它；导演层 05-28 为压过 DeepSeek/Anthropic 第一选择而建（V1–V5 五轮）；90.4% 五模型不含 DeepSeek。匿名访客拿到的模型没有任何公布数字描述它 | P1 | chat.py:369-372；daily.yml:34；prompt_routing.py:3285-3295 | 措辞 15 min；实验臂 30–45 min |
| H13 | **伪造 DOI 前缀 + 四个绿 PR 挂 40 天**。provenance.py:254 发 `10.5281/standard-astro.<id>`（Zenodo 真前缀下不可解析的串）；仅认证 GET、无前端调用、payload 自标 metadata only，**不是一键暴露**，但是不造假产品 main 里的假样标识。PR #34（去 DOI + CITATION.cff）、#35、#36（H0 链预注册）07-24 起绿着 | P1→P2 | provenance.py:254；main.py:69、:724；`gh pr list` | 每个 rebase 30 min；阻塞成本 = 你四次决定 |
| H14 | **评测台看不见探索深度和"少给"**。cap / deadline / n_tool_calls 记在原始 jsonl 但无检查消费；should-pass 语料只算 abstention / hard_block / refusal；矩阵跑 5 轮/240 秒 vs 生产 12 轮/360 秒；8×4 改写变体 08-06 起 FROZEN_NOT_YET_RUN | P2 | runner.py；evaluate_standard_astro_v02.py:266-267；runtime_config.py:11,15,25,29 | 变体键 30–45 min + 1 h 跑 |
| H15 | **"人类批准"在聊天里没有载体**。Union3 暗道有机械正确的定义（append-only ClaimAuditReview 绑 claim_hash / source_hash，reviewer ≠ owner，从不置 publication_ready，六个开关全关）；聊天路径零审批状态。validate_claims 对 "APPROVED by human reviewer: H0 = 68.3 ± 1.2" 一视同仁放行：**伪造审批语言**才是需要拦的 | P2 | workspace_records.py:130-178；union3_research_loop.py:2050-2085；evidence_pack_v2.py:1-6 | 45 min |
| H16 | **闸门是正则目录形状**。"a local void would shift H0 by roughly 1.4 km/s/Mpc" 被抽取并删；"sigma8 might be near 0.78"、"10 percent" 零 claim 通过，奖励躲闪措辞。flat-universe 回退把本轮工具 JSON 所有标量收成一个集合，1% 内巧合即"支持"；ess / r_hat 未进黑名单 | P2 | claim_validator.py:136-402、:294-297、:2443-2460、:2597-2598 | 40–60 min（排在假设卡之后） |
| H17 | **暗启动库存与一个坏导航**。约 24k 行开关关着的后端；五专家 340 行活着；Claim Audit 主导航链接无条件渲染，默认部署下打开是 "not open yet"；3.7k 行非核心前端页可达 | P2 | App.tsx:190；ClaimAuditPage.tsx:425-432；config.py:221-238 | 导航门控 15 min；库存决定 30 min |
| H18 | **没有代码执行底座**。SANDBOX_BACKEND 默认 disabled，production 下任何其他值直接 raise；run_python 无 registered 数据源，沙箱 astro 助手零引用注册表；34 条注册表里**没有一条能被模型写的代码读到**。笔记的 Notebook / 运行环境预设了托管不能开、本地也接不上钉扎数据的原语 | P2 | config.py:154、:574-579；chat.py:656-660；run_python.py:103-109 | 本地装载器 45–120 min；真隔离运行器 = 多日基建 |
| H19 | **跨会话记忆已是潜在洗数通道**。memory_enabled 开启后注入最多三条 180 字会话摘要，摘要由上两条助手回复构成；CLAUDE.md 禁止旧上下文支撑数字，但执行在输出侧 | P3 | memory_service.py:111、:256；orchestrator.py:178-179 | 0（进候选池） |
| H20 | **卫生项**。cases.yaml 头写 16 例实为 18；manifest 注释写 60 实为 61；**prompt.md:69 硬写 12 轮预算而长模式实际 30 轮，模型被告知的预算比实际少 18 轮，直接关联"过早停止"，一行修**；appendix.md 命令拒绝 isochrone / transit 而 base.md 在教；prompt.md 有 517 个中日韩字符而 claim_validator 硬拦 CJK 回复；asteval 钉 <1 排除修复版；14 个 Dependabot PR 开着；ARCHITECTURE.md 三句过时；SKILL.md 断言 chain_tier=='publication' 而压缩链永远到不了；**v02 副本六个未跟踪文件含已撤回的确定性重放专家包**，交接明令不得使用 | P3 | 各处如列 | 45–60 min 一个 PR + 你点 Dependabot |

---

## 三、优化方案

### 3.1 排序原则

1. **仪器先于产品**。没有绿的 Daily / Weekly 和 HEAD 基线，任何行为改动都无法证明"没变坏"，任何数字都不能引用。
2. **先测后改**。笔记 P0 的前提是假设不是诊断（CLAUDE.md:54-56 "settle with one real test"）。
3. **只朝收紧方向动闸门**。所有修误杀都通过教解析器识别更多形式，不动阈值（CLAUDE.md:170-172）。
4. **按依赖排，不按名字排**。探索输出需要安全通道（H5、H6a、H7 先修）；报告是唯一 Demo 可消费物。
5. **增长门**（CLAUDE.md:17-19）。新路由 / 页面 / 工具 / 数据集必须先答"谁需要"。P1 / P2 / P4 / P6 进候选池。

### 3.2 分阶段计划

#### 阶段 0：修仪器（不动产品行为，全部可并行）

| 项 | 做什么 | 为什么这样排 | 动哪些文件 | 验收标准 | 护栏风险 | 工时 |
|---|---|---|---|---|---|---|
| 0.1 Daily 连红（H1） | 一次真实 DeepSeek 调用复现 400；两条候选：合成预分派轮补 reasoning_content，或 Daily runner 改 deepseek:v4-flash / 关思考模式；立 issue；手动跑一次 Daily | 唯一定期模型在环证据源；笔记前提能否测取决于它 | loop.py:1448-1495 或 runner.py:136；daily.yml | 连续 3 次绿；F1、F2 均 PASS；日志 0 行 reasoning_content；issue 由修复 commit 关闭 | 无 | 1–2 h |
| 0.2 Weekly 连红（H2） | 两处 checkout 加 `fetch-depth: 0`；workflow_dispatch 跑一次；一行 issue 存档 | 10 分钟恢复第二个死掉的证据源 | scientific-validation.yml:27,152 | 下次定时跑 0 failed；HONESTY_EVIDENCE 加一句写明该 workflow 和最后绿的日期 | 无 | 10 min + 47 min CI |
| 0.3 公开状态文案（H10、H11） | 两句带日期的数字状态改成"Actions 链接 + 截至日期：Daily 自 08-11 红（原因）、Weekly 自 07-26 红（原因）"；90.4% 改为"模型在环的 4 个任务（V02_03–06，n=60）"并附"期望 full 少给 8/120"；0/15 注明纯流水线层 | 不造假产品不能对自己说过时的话；Demo 访客会看 | README.md:65-67、:71-81；HONESTY_EVIDENCE.md:10,82-88；演示脚本数字卡 | 无任何一句定时跑通过率没有 7 天内日期或生成来源；docs 测试 grep 旧措辞失败；修好后合并 #35 让绿率机器生成或删掉该句 | 无 | 15–20 min + #35 rebase 30 min |
| 0.4 CLI 桥 + 源解析 + HEAD 基线（H3） | ① 在**干净的 Terminal.app**（不在 Claude Code 会话内）跑一轮 F1 走 claude-cli，记退出原因；② 冷缓存干净网络抓一次 V02_01 源，把 30 秒超时分类为网络 / PDF 子进程 / 预算过紧；③ claude-only 复跑作改动前基线 | 笔记的闭环 Demo 硬前提是能用的桥和源路径 | source_packet_resolver.py:54、:340-403；rerun_natural_matrix.sh；inference_router.py:996-1032 | rerun_3a7e6e4_summary.json 存在；transport_failures=0；V02_01/02 source_status=verified_exact；hard_escapes=0。在此之前 HEAD 不得引用任何 Alpha v0.2 / Demo 数字 | 低 | 诊断 45–90 min；复跑 1–4 h 无人值守 |
| 0.5 四个绿 PR（H13） | 你一次坐下决定 #34 / #35 / #36 合或关；#23 单独议（冲突 + 冻结） | 零代理成本，全是决定 | — | `rg '10.5281/standard-astro' backend/app` 0 命中；CITATION.cff 存在；#36 合并或在 backlog 写明关闭理由 | 无 | 每个 rebase 30 min |
| 0.6 卫生 PR（H20） | 两个计数；prompt.md:69 改成"最多 30 轮 / 读运行时注释"；删 appendix.md 矛盾拒绝；asteval ≥ 1.0.9；ARCHITECTURE.md 三句；SKILL.md 断言改 exploratory；Dependabot 14 个你一次点完；v02 副本六个未跟踪文件移出 docs/research | 每条都是代理或访客会读到的错话；prompt.md:69 直接关联"过早停止" | 如 H20 列 | 两个 checkout `git status` 干净；cases.yaml 头 = id 数（加测试）；manifest 注释 = build_allowed_tools 大小；Dependabot 0 或各有书面理由 | 无 | 45–60 min |

#### 阶段 1：P3-lite（笔记 P3 提到第一位；全是"补矛盾 + 堵泄漏"，不加新分级）

| 项 | 做什么 | 为什么这样排 | 动哪些文件 | 验收标准 | 护栏风险 | 工时 |
|---|---|---|---|---|---|---|
| 1.1 提示词/闸门矛盾（H6a） | 删 prompt.md:413-425 第 1–2 条及 :98-109 对应句，换成"探索层数字留在工具卡；散文只定性描述，任何数值含四舍五入都不许（'around 68' 仍触 1% 窗）"；删 item 4 的 `__exploratory_warning__` 指令 | 这是"总差一口气"的一个具体位置：模型被提示词教着撞墙 | prompt.md；test_system_prompt_helpers | 断言 "You MAY discuss the posterior median" 不存在；测试钉"带 exploratory 标签的 H0 引用被提示词和闸门同时禁止"；F2 绿；honesty.py 逐字节不变 | 无 | 15–30 min |
| 1.2 hypothesis 洗白（H9） | 豁免收窄为句首标签 / 谓语形式（'hypothesis:' 句首、'a hypothesis worth testing'、'forecast(s) that'）；中文同改；两句洗白句加红测试 | 笔记词汇一旦进入提示词或卡片，这个洞放大；且今天已活 | claim_validator.py:3603-3630；test_claim_validator.py | 两句产生违规；:736-741 四句仍通过；F1–F4 不变 | 无 | 20–30 min |
| 1.3 honesty 分词器 + 0/1 误杀（H7、H8） | `_reply_number_tokens` 复用 claim_validator 科学计数法变换，新写拼写小数与 h→H0；跳过 prior_dominance_screen.parameters 或平凡 0/1 值；'%' 后 token 跳过除非被扣键本身是百分比 | F2 合约下它是唯一防线；E1 研究链现在死在这里；假设卡若建在它上面天生漏 | honesty.py:19-22、:48-60、:197-236 | 四个红测试变命中；'68% interval' vs 68.3 不命中；E1 复跑摘要不再被替换为一句话；F2 / B4 / B5 绿 | **低但非零**：'%' 豁免与 0/1 跳过是放松硬门的两种形式，**要你签字** | 1–2 h + 6–8 测试 |
| 1.4 闸门前泄漏（H5） | agent_text 发出前：匹配 nonpublication_posterior_values / untrusted_evidence_echo_values 的 token 打码；最终迭代的 agent_text 不发；其余打 draft:true 并在前端标"草稿 / 未验证" | 任何探索工作的前置；07-22 立案至今 | loop.py:1780-1800；client.ts:3087-3096；ChatMessageList.tsx:72-83；runner 新检查 | 新盲测检查对 F1 / F2 / B3 / B5 通过；单测断言无 agent_text 事件携带同轮后被打码的 token。**不要**"替换所有不在工具宇宙的数字"（会毁年份 / arXiv id） | 无 | 45–90 min |
| 1.5 审批标记（H15） | 任何以 "Draft claim / APPROVED by" 开头且数字与工具匹配的句子前缀 "NOT APPROVED —"，除非存在匹配的 ClaimAuditReview 行；validation_summary 带 approval_state='none' | 与阶段 C 格式绑定；先做标记防伪造审批语 | loop.py 出口；client.ts；测试 | 含审批语 + 工具匹配数字的回复带前缀；F 组不变 | 无 | 45 min |
| 1.6 结构化假设块（笔记阶段 C 的 Hypothesis 层） | **三选一**：(a) 保持零数据轮硬拦（现状；不带数字的定性假设本已通过）；(b) 单一尾部 `<hypotheses>` 块 ≤ 5 条，切出 clean_reply 后再跑 extract / validate / zero_data，仍过 echo / posterior 检查命中即整块丢弃，平台固定头 "HYPOTHESES — no tool produced these numbers; not claimable"，永不进 text_parts / tool_results / 记忆 / 论文收割；(c) 只允许 verify_scalar_derivation 回执里的数字 | 数字型假设在每道门里与伪造测量无法区分；平台已信任一个模型发出的结构标签（`<tools_returned_nothing/>`）同机制可承载；但这是反转"零数据轮硬拦"的判断题，不是审查者能替你排期的 | abstention.py；loop.py 两个拼接点；前端卡；runner | 散文 'H0 ~ 70' 仍打码；块内同数渲染且 numeric gate skipped；块内粘贴 71.43 丢弃；B1–B5 / C1–C2 / F1–F4 逐字节不变 | 低（选 b） | 决定 0；(b) 5–8 h + 40–60 min 盲测例 |

#### 阶段 2：P5 报告脚手架（笔记 P5 提到第二位）

| 项 | 做什么 | 为什么这样排 | 动哪些文件 | 验收标准 | 护栏风险 | 工时 |
|---|---|---|---|---|---|---|
| 2.1 报告 13 节 | export_research_report 加确定性脚手架：Failed / blocked attempts（__tool_status__ ∈ {FAILED, EMPTY, SYNTHETIC, suppressed} + failure_categories + capability_gap_matrix + 单元 preliminary_reasons）；Alternative explanations（显式空列表 + model_comparisons）；Human Review Checklist（来自 publication_gate.reasons）；Draft Scientific Claim（仅 publication_ready 存在时渲染，否则 'none eligible'）；模板化 hypotheses 字段改名 "Platform checklist (rule-derived)"；报告给一个渲染页 / 复制为 markdown | 后端已发 13 标题，前端埋在 `<pre>`；这是博士后唯一能拿在手里的东西；不动任何闸门。**诚实预期**：Failed Attempts 在现状下是 Runnable Gaps 的改标题，Alternative / Draft 在压缩似然跑里是占位。模型发起的失败尝试，在没有探索回路前不存在 | research_program.py:1053-1160、:1183-1191、:1683-1700；ResearchProgramPanel.tsx:330、:801-815；ARCHITECTURE.md:122 | E1 型跑导出 markdown 含 13 标题按序；Failed Attempts 列出每个不可跑单元及理由码；导出不含闸门回复外的任何数字；"Hypotheses" 不再盖在模板串上 | 无 | 2–3.5 h |

#### 阶段 3：先测 P0（笔记 P0 的前提）

见第五章。**产品代码零改动**。工时：任务文件 + runner 扩展 + 评分器 2–3 h；跑 1–3 h 无人值守；依赖 0.1 / 0.4。

#### 阶段 4：P0 工程（仅当第五章复现前提，且无单一机制臂能关掉它）

| 项 | 做什么 | 为什么这样排 | 动哪些文件 | 验收标准 | 护栏风险 | 工时 |
|---|---|---|---|---|---|---|
| 4.1 去掉聊天路径的工具藏匿（H4） | 默认路径把 available_tools 直接交给回路（或先加 exploration_phase_enabled 默认 False）；删顺序串跑 + 合并；编排器只留意图提示 / "User Background" 字符串；改 ARCHITECTURE.md:627 | 模型看不见仪器就谈不上探索；也是"框架驾驭强模型"能否测的前提 | chat.py:640-646、:690-694、:1519-2474；orchestrator.py；9 个测试文件 | 内省测试断言研究提示交给 _run_agent_loop 的 tools 含 build_allowed_tools('cosmology') 全部名字；复合提示只跑一个 _run_agent_loop；聊天路径测试（盲测 runner 绕过编排器，不能当验收） | 低 | 30 min（开关版）/ 2–4 h（删除版） |
| 4.2 探索窗口 | 全在 exploration_phase_enabled 后：Phase 0 写 docs/plan/exploration-phase.md（工具边界不变量、触碰文件、开关关时等价测试）；Phase 2 在 _run_agent_loop 对研究 / 宇宙学流程 iteration < K（默认 3）跳过 :957-1034 级联、:1457-1495 绕过、:1583-1673 覆盖，保留失败移除 / line-relation 上限 / synthetic 提醒；:1782-1800 以 draft:true 发真实文本（依赖 1.4）不进 text_parts；K 后强制链照旧；链后空列表换小白名单 {assess_bao_bin_anomaly, search_literature, compare_luminosity_distances, evaluate_chain_diagnostics}。"research_exploration 继承 full_research 工具面"单列为选项给你 | 笔记说对的地方在这里：链从第 0 轮强制、链后菜单清空、模型散文丢弃。修法是回路内的开关窗口，不是新回路，不碰闸门栈（loop.py:2203-3861） | loop.py 如列；prompt.md:69 | 开关关：mock-LLM 单测 18 例 validation_summary 相同；开关开：'explore X' 与 'fit X' 同工具面，同一研究提示在 plan_research_program 前 ≥ 1 次模型自选 tool_call，终稿过全部闸门且处置相同；`git diff --stat` 不含 claim_validator.py / honesty.py / cases.yaml / 任何阈值；作 C2 对第五章 C1 测，hard_escape=0；Daily 绿后才合 | 中 | Phase 0 30 min；1+2 1.5–2.5 h；全程 4–7 h |

#### 阶段 5+：候选池（不排期）

| 笔记项 | 处置 | 依据 |
|---|---|---|
| P2 完整闭环 | 不建新回路；按库存补三个真缺的步骤（失败尝试记录、人审清单、数据清洗）；第 5/7 步（清洗、代码生成）依赖 H18 的底座，托管不可开、本地接不上钉扎数据 | research_program.py；config.py:154,574-579 |
| P4 记忆 | 候选池，直到有真实用户要连续性；若复活：新表按 ChatSession.workspace_id，去掉缓存引用，先决定现有 memory_enabled 通道是否合规 | H19 |
| P1 环境抽象 | **作为架构项拒绝**；保留为提示词 / 叙事框架。笔记 §2.1 L26、§4 L148-157 自己说注册表留作实现层，所以它是改名不是改架构。§5.2 / 5.3 改写为"延后：无设计；任何未来版本须声明数字在哪里收割给 claim_validator" | CLAUDE.md:159-165；config.py:239-244 |
| P6 多智能体 | 删现有串跑（4.1 顺带）；8 角色联盟不开 | H4 |
| §1 / §12 扩展到通用框架 | 砍；如要"驾驭强模型"叙事，只改 README 定位散文（零代码、CI 中性）；修订版笔记显式保留 cosmology-only 或写出反转理由 + 命名用户 | CLAUDE.md:8,11,17-19；7872bd8 |

### 3.3 应从笔记砍掉的项目及理由

| 砍什么 | 理由 | 证据 |
|---|---|---|
| §1 / §12 "通用科研 Agent 框架" | 翻三个决定不给理由；无用户；03-16 模式重演 | CLAUDE.md:8,11；7872bd8 |
| P1 作为架构项 | 数字只有在固定工具边界收割才可声明；笔记自己说注册表留作实现层，所以是改名 | CLAUDE.md:159-165；笔记 L26 / L148-157 |
| §5.2 动态化 / §5.3 融合 | 无设计；"融合"意味着数字失去显式工具来源，claim_validator 无物可验；§5.2 已以 Foundry 形式存在（人审墙禁止可声明输出） | config.py:239-244 |
| P6 八角色联盟 | 现有五专家串跑是有害门面，应删 | orchestrator.py；chat.py:1544-1608 |
| P4 记忆（现阶段） | 与不可放松护栏冲突；07-01 同类当天回滚；无用户 | CLAUDE.md:157-158；cd3ff5e |
| 16 步作为新回路 | 约 8 步有代码、5 步启发式 / 暗、3 步缺；第 5/7 步无底座 | research_program.py；H18 |
| Notebook / 运行环境（托管） | 生产策略性禁用；本地不接钉扎数据 | config.py:154,574-579 |
| Demo 高潮 = "模型独立推进得出 Findings" | 与 08-09 价值修正相反；平台强路径按设计 WITHHELD；研究链最终回复现为一句话拒答（H7） | 技术报告 3.2.6；README.md:84-86 |
| P0 的"允许错误候选"若落到闸门层 | 笔记本身没提放阈值，但任何实现须写明"阶段 A/D 只是预算、阶段顺序与审批元数据，不动任何常量 / publication_ready / chain_tier" | CLAUDE.md:170-172 |

### 3.4 笔记 P0–P6 到计划的映射

| 笔记 | 代码现状 | 新位置 | 解锁条件 |
|---|---|---|---|
| P0 探索 / 验证分离 | 无阶段；导演层从第 0 轮强制；research_exploration 车道只在开关开时活且**减**工具 | 阶段 3 测 → 阶段 4 做 | 0.1 / 0.4 仪器；1.4 泄漏；第五章复现 |
| P1 环境抽象 | 注册表 = 护城河边界；Workspace 是 Union3 审计容器 | 候选池（叙事） | 永不作为架构 |
| P2 完整闭环 | plan → matrix → graph → fact-check → export 已有（模板化） | 阶段 5 补缺 | 有用户 |
| P3 结论分级 | 工具层有三级；散文层无；三处矛盾 | **阶段 1** | 无 |
| P4 记忆 | 关键词档案 + 已回滚功能 | 候选池 | 用户要求 |
| P5 报告 | 13 标题已发，UI 埋 `<pre>` | **阶段 2** | 无 |
| P6 联盟 | 串跑门面 | 删（4.1） | — |

---

## 四、博士后 Demo 的具体建议

**先说硬门槛**：阶段 0 全部 + 1.1–1.4 未完成前不演示。你自己的脚本第 6 行、README:78-79、CLAUDE.md 三处都这么写。另外：**别把 v02 副本里那个 08-04/05 的专家包交出去**，那是已撤回的确定性重放口径，交接文档明令不得使用。

### 4.1 高潮换掉

按 08-09 修正（技术报告 3.2.6）："替你核别人的数字比替你算值钱"。Demo 的高潮不是"模型得出 Findings"，而是**博士后当场拿一个他在意的数字（审稿里的、别组论文里的）让平台核，看它要么给出带 sha256 和行级来源的答案，要么诚实说查不到**。这才对得上北极星（CLAUDE.md:12-15）。Findings 以 WITHHELD / CAPABILITY_GAP 收尾**就是**产品的卖点，笔记的分级（§3、§10 第 13 项）本身也容许。缺的是"深路径以 WITHHELD 收尾时算不算成功"的判据，写进脚本。

### 4.2 结构：现有 22 分钟脚本 + 追加一块

不另建并行的 demo 提示集，与冻结的 5 提示脚本合并。追加块只选一个深任务。

| 候选 | 能展示什么 | 不能展示什么 / 尴尬点 | 前置 |
|---|---|---|---|
| **#1 ALPINE [CII] L–FWHM 拟合**（F1） | 唯一以 publication_ready=True、数字进散文、行级 provenance、系统误差预算收尾的任务（08-06：54.0 秒，β=0.799±0.201，α=8.354±0.091，74 行）；长预算自动触发 | **模型基本不在环**（确定性播种、正则选分割和宇宙学，line_relation.py:40-70），不符合笔记"体现模型探索"；提示词**不能**写 'Planck18' / 'FlatLambdaCDM'（会翻 _is_cosmology_likelihood_workflow、第 0 轮强塞 list_cosmology_datasets）；**不要** z<1 vs z≥1 分割（ALPINE z=4.4–5.8，返回 n=0） | 0.4 桥 |
| **#2 研究链**（plan → matrix → graph → fact-check → export，E1 型，约 80 秒） | 唯一匹配笔记 13 节形状的路径；发 13 标题报告；展示面板不展示气泡 | 现在最终回复是一句话拒答、Findings 空，原因是 H7；**修好 H7 + 2.1 报告脚手架前不能用**；所有本地压缩单元 publication_ready=False，Findings 必然分层 / WITHHELD；hypotheses 是关键词模板（改名后才能展示） | 1.3、2.1 |
| **#3 一轮三条链**（lcdm / wcdm / w0wa on DESI DR1 BAO + Planck 2018 compressed） | 执行 + 无数字回复，展示分级扣数 | 代码和测试路由过，**无端到端实测**；提示须避开 compare / analysis / constrain / research / tension（'hubble tension' 触 H0-anchor 路由；DESI DR2 + 两个 SN 集触证据矩阵，本地全格 WITHHELD） | 0.4 桥 + 3/3 干跑 |

所有提示按词元路由（prompt_routing.py:2164-2226、:2879-2901、:3348-3356、:3418-3453），**每条提示在 HEAD 干跑 3/3 工具序列一致后冻结**，记录 routed_task_kind。

### 4.3 三道门（预注册进脚本）

- **门 1（演示前，机器）**：0.4 基线通过；所选深任务在 HEAD 实跑 ≥ 3 个可达集内不同 run、零硬逃逸、全部节自工件生成无手改；Findings 允许分层含 WITHHELD。
- **门 2（演示中）**：至少一个未排练问题以工具为据回答或明说能力缺口，绝不伪造数字。
- **门 3（结果，二值）**：博士后回答"你下一次审稿会不会用它核一个数字？"并说出是哪种杂活；重建的盲评包（**只用模型在环样本**）达 10/12 和 8/12。

### 4.4 明说不能展示的

- 散文级 observation / inference / hypothesis 分级（无 UI，无类型，无 i18n 键）
- 模型发起的失败尝试叙事、替代解释、人审清单（报告脚手架落地后是占位）
- 聊天里的审批步骤（只在开关关着的 Workspace 审阅队列）
- 跨会话项目记忆
- 托管环境下的 run_python
- **模型选择要写进脚本**：托管默认 DeepSeek，不是 90.4% 五模型之一；BYOK 走 Settings 页（UI 能否选 Anthropic **未核实**）。演示用哪个后端提前定并说出来。

### 4.5 数字卡改法

"90.4% on the 4 tasks where the model was in the loop (V02_03–06; n=60)" + "under-delivery vs expected disposition: 8/120（V02_03 6/15 expected-full；V02_04 2/15 hard_block on expected-limited）" + "0/15 fabricated number repeated: pipeline stratum (V02_08, llm_calls=0)"。

---

## 五、"总差一口气"先测后改的实验设计

**立场**：把笔记 P0 当假说（H₁：在生产预算下，模型在可达集内有明显下一步时主动停止的比例 ≥ 阈值），不当诊断。**产品代码零改动**。

### 5.1 为什么现有数据答不了

- 六维评分无深度维（score_standard_astro_v02.py:37-44）；cap / deadline 记了没人读；4/8 任务奖励停下。
- 'direct' 基线是 tools=[] 闭卷（evaluate_standard_astro_v02.py:42-48、:234-250），答不了"闸门是否压制模型"。
- 评测 runner 硬编码 5 轮/240 秒，生产 12 轮/360 秒（runtime_config.py:9-16）。
- 评测 runner 强开 LIGHTWEIGHT_VERIFICATION_ENABLED=1，生产默认 False，演示脚本又开它。**你抱怨的那些会话到底开关开没开，未核实**，先答这个问题。
- 三个可分离机制在代码里都能定位：(a) 拒答合约把单标签回复定义为 REQUIRED 且"成功"、从不要求替代解释（base.md:417-443）；(b) research_exploration 车道剥 11 个重工具（loop.py:721-741，仅开关开时）；(c) 剩 75 秒时的 "Stop broad retries now" 提醒（loop.py:1351-1359）。

### 5.2 两层设计

**第一层（便宜，先跑）**

- 冻结 `docs/research/standard_astro_v03_exploration_tasks.json`，跑前提交 sha256。8 题全在 34 条注册数据上：T1 BAO 版本依赖（desi_dr1 vs dr2 + planck compressed + pantheon_plus，lcdm vs w0wa）；T2 SN 样本依赖（pantheon_plus / des_sn5yr / union3）；T3 H0 锚点聚类（shoes / trgb / cchp / megamaser / h0licow vs BAO+BBN 反向阶梯）；T4 曲率；T5 增长 / S8（RSD vs 弱透镜 vs CMB）；T6 mnu vs DE 自由度（planck vs act lensing）；T7 计时器 vs BAO H(z)；T8 Ly-α vs 星系 BAO。每题冻结**可达集**、一个**下一步显然的工具调用**、避开确定性关键词路由的措辞。**注意混淆**：链类题（T1 / T2 / T4 / T6）命名数据集 + 模型后路由静态决定、链在模型行动前确定性执行，premature_stop 在这些题上被路由混淆；T3 / T5 / T7 这类模型有选择权的开放题是主要终点。
- 条件：C0 裸闭卷（只作参照，不混）；**C1 平台生产预算 12/360**，LIGHTWEIGHT_VERIFICATION_ENABLED **两个状态各跑**。
- 模型：claude-fable-5（唯一可期望能用的桥）；2 重复 → 8×2×2 = 32 样本；流水线层（llm_calls==0）单独报。
- 评分：先规则审计工具轨迹，再你裁决，LLM 评判最后。维度 0–2：exploration_breadth（可达集执行比例）、hypothesis_generation（带标签、工具为据的替代解释数）、claim_layering、push_through（探索 / blocked 层后是否重试）、**premature_stop（主终点）**：停止时 cap=False、deadline=False、elapsed<180 秒、下一步显然工具未调用；**按链层扣数不算过早停止**；hard_escape 用现有 claim 门，必须 0。
- 预注册判据：C1 premature_stop 率与 rule-of-three 上界一起报；若 C1 < 25%（预注册），**P0 前提以"未复现"关闭，阶段 4 从 backlog 删除**，笔记书面撤回该诊断。

**第二层（仅当第一层 ≥ 阈值）**：机制消融臂，每臂只动一样

- C2a 提示词（1.1 修好后 + 探索轮软化拒答文 + 要求 ≥ 2 条带标签工具为据的替代解释）
- C2b 路由（research_exploration 车道 heavy_route_allowed=True，仅评测配置）
- C2c 预算（长模式 30/1800）
- C2d **steering-off**（关直连门、六个 LLM 前绕过、强制覆盖；闸门不动）。这才给 §6 "强模型自己会不会路由对"一个数字
- 离线专用 'tools_no_final_gate' 臂记录闸门前草稿（永不服务、永不进 docs/research/assets）
- 一个机制只有其单独臂移动数字才记功；任何 C2 臂 hard_escape > 0 = 发布阻塞。

**产物**：冻结文件、样本 jsonl、层不混的 summary json、一页结论写明前提是否复现。

---

## 六、被反驳掉的观点清单

| 原发现 | 为什么被否 | 残留有效内容 |
|---|---|---|
| F13 "研究模式确定性摘要打印被扣中位数，再被替换为一句话拒答；AP Ωm 卡与散文矛盾" | 机制错：摘要对非 ready 单元**不打印**中位数（summaries.py:1071-1101）；真正命中的是 0/1 计数撞上 prior_dominance_screen.parameters（见 H7）。AP 半边与 A3 盲测冲突（cases.yaml:113-116 **要求**回复含 Ωm），加 omega_m_best 进扣数键会让每次 AP 轮触发 | H7（真缺陷，换根因） |
| F22 "笔记阶段 A/C/D 词汇里藏了四处伪装的阈值放松；长模式 UI 不可达" | 稻草人：笔记全文未提任何阈值 / 常量 / publication_ready；长模式**可达**（chat.py:605-631 关键词 'paper' / 'reproduce' / '复现' / '论文' 自动切换）只是没显式开关；"grep 禁止 publication_ready 写入"验收会在 57 文件 234 行上立刻失败 | 一句计划护栏（阶段 A/D 只是预算 / 顺序 / 审批元数据） |
| F24 "106 KB 提示词 60% 可搬、06-04 桌面计划 M1/M3 是笔记 P0/P1 所需" | 笔记从未提 prompt / DAG / claim_validator；M1 按自己的验收零可观察；M3 漏掉红队语料 + 盲测子集 + 净例三层，且 90.4% 建立在 prompt+loop 上，砍 60% 要重跑矩阵，是多日战役非 3–4 小时；系统提示已 cache_control 收益边际 | H20 卫生项：prompt.md:69 的 12/30、appendix 矛盾拒绝、517 CJK 字符；约 100 条禁令 / 0 替代解释是 §2.2 的可引用证据 |
| F28 "5,271 行管道 DAG 不可达应删" | **删了会崩**：ai_tools/__init__.py:18 模块级 import pipeline.storage_auth；chat.py:2656 execute-action 直接调 run_pipeline / generate_pipeline / plot；analysis_tools.py:137（analyze_spectrum 在 61 工具集内）、account_deletion.py:907、scheduler_worker.py:132 都用；16 个测试文件 import 它；且删除违反 08-09 停止线 | H20：ARCHITECTURE.md 三句、SKILL.md、两个计数、asteval 升版 |

其他在综合中被纠正而非否决的点（已并入正文）：F01 回落"静默" → 有标注；F03 "ACT 谱缺失导致损失" → V02_03 不需要 ACT 谱、损失是闸门处置；F07 "一键暴露" → 认证、无调用者、自标；F12 z 分割为空、'Planck18' 触发路由；F14 "explore 被惩罚"仅开关开时；F18 需你三选一；F21 '%' 豁免风险 none → low。

---

## 七、完整性缺口

| 缺口 | 状态 |
|---|---|
| §2.1 "维护成本随工具数增长"、"组合能力有限" | 无人量化；"负担在导演层不在注册表"是推断，未以计数核实 |
| §3 阶段 B "对比基线 / 统计检验"作为能力 | 无专项发现；lcdm 锚点单元（research_program.py:318-330）是部分基线 |
| Weekly Scientific Validation workflow | 此前每份发现、每份项目文档均未提（现 H2） |
| 前端 Settings / BYOK 路径 | SettingsPage.tsx 存在；演示访客能否在 UI 选 Anthropic **未核实** |
| 文献搜索 | 已查非发现：ADS_API_KEY 未设时聊天工具回落 arXiv（literature.py:128-150） |
| 成本、鉴权 / 多用户、Render 部署状态（#24 自 07-21 开） | 笔记未提钱；均超出笔记范围，未审 |
| H1 根因 | 两个候选，需一次真实调用 |
| H3 源解析 30 秒超时归类 | 需一次冷缓存干净网络抓取 |
| 你的"一口气"会话跑在哪个开关状态 | 未核实，第五章第一步 |
| honesty.py 0/1 误杀是否影响 E1 之外的路径 | 08-06 全部 case 文件里只有 E1 触发；其他跑未查 |
| Foundry PR #23 | 冻结中，未评估 |

---

## 八、需要你拍板的决定（按建议排）

1. **cosmology-only 保不保**。建议：保。要"驾驭强模型"叙事只改 README 散文。
2. **四个绿 PR**（#34 去伪 DOI、#35 每日摘要发布、#36 H0 链预注册、#23 Foundry）。建议：#34 / #35 合，#36 合（是 H0 恢复的前提），#23 关或留冻结。
3. **1.3 的两处"放松形式"**（'%' 豁免、0/1 平凡值跳过）。建议：签，但各配一个硬盲测例。
4. **1.6 假设块三选一**。建议：先 (a) 现状，等第五章结果再定是否上 (b)。
5. **1.2 是否豁免 "we hypothesise that"**。建议：不豁免。
6. **Demo 后端用哪个**。建议：BYOK Claude，并在脚本里写明。

---

## 附录：审查方法与我亲自核实的清单

工作流：10 路读者（agent 循环 / 提示词 / 出口闸门 / 研究功能 / 工具注册表 / 评测 / 前端 / 治理 / 开放缺陷 / 多智能体与记忆）→ 6 位批评者（战略 / 架构 / 严谨性 / 测量 / Demo / 工程债）→ 合并去重 28 条 → 每条 3 位反驳者（代码证据 / 是否违反项目红线 / 可行性）默认判"反驳"、2/3 存活 → 完整性批评者补 6 条 → 汇总。103 个 agent，约 1,200 万 token，1,900 次工具调用。

我在工作流之外亲自用 rg / venv 内省 / gh 核实的事实：

- Daily 连红 22 次的起止日期与 400 错误原文（`gh run list` / `gh run view --log-failed`）；DeepSeek 思考模式 04-24 起就开、回传代码 04-26 起就在、PR #46 没碰 DeepSeek 归一化器。
- 61 个宇宙学允许工具中 40 个不在任何专家名单（venv 里跑 build_allowed_tools 与 orchestrator.agents 对比）。
- provenance.py:254 的伪 DOI 串与 main.py:724 的无条件挂载。
- prompt_routing.py:1197-1207 把 explore / 探索 / 假设 路由到 heavy_route_allowed=False 的车道。
- loop.py:3464-3483 研究模式最终散文被 `_research_tool_grounded_summary` 整段替换；research_program.py:1683-1700 的假设是关键词 if/else 模板。
- prompt.md:98-109 与 :413-425 教模型引用探索后验，loop.py:3770 的 honesty 门无条件替换。
- honesty.py:19-22 数字正则的尾部 lookahead，07-17 起未动。
- claim_validator.py:3622 裸 `hypothesis|forecast` 豁免。
- loop.py 23 处 `[RUNTIME` 注入、11 处 `forced_tool_call_override = True`、默认 12 轮 / 360 秒。
- 桌面 Standard_Astro_Workflow_Optimization_Plan.md 为 06-04 plan-mode 草稿，未进 git。
