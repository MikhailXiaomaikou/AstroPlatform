# Solar-system M0 盲测

## 一行启动(全跑 20 个 case)

```bash
cd backend
source .venv/bin/activate
export ASTRO_RESEARCH_FOCUS=solar_system
export ANTHROPIC_API_KEY=sk-ant-...           # 你自己的 key
export CLAUDE_MODEL=claude-sonnet-4-6         # 可选, 默认 sonnet-4-20250514
python scripts/blind_test_m0/runner.py
```

跑完会输出到 `scripts/blind_test_m0/results_<时间戳>/`:
- `case_<id>.json` — 每个 case 完整 trace(events / tool 调用 / 最终回复)
- `summary.md` — 汇总表 + 期望 vs 实际工具对比

## 只跑一个 case (验证 pipeline)

```bash
python scripts/blind_test_m0/runner.py --case A1
# 或跑某一组
python scripts/blind_test_m0/runner.py --group A
```

## 预估花费

- 20 case × ~10 turns × ~5K input + ~2K output tokens
- Sonnet 4.6: $3/1M in + $15/1M out → 总 **~$1-2**
- Sonnet 4: 类似
- Opus 4.7: ~$5-10

## 预估耗时

- 每 case 30s-2min(LLM thinking + tool 调用 + 真实 connector 联网)
- 20 case 顺序跑约 **15-30 分钟**

## 跑完之后

把 `results_<时间戳>/` 路径发给我,我做 summary 分析 + 失败 case 修复建议。

## 20 case 速览

| 组 | 数 | 测什么 |
|---|---|---|
| A | 5 | 金路径:Phaethon / Apophis / 67P / Ceres / Vesta |
| B | 5 | 反幻造攻击:诱导编 MOID / 假引用 Bowell / user_supplied 谎言 / 错领域 / sandbox 绕道 |
| C | 3 | Honest abstention:出 scope / 100 年 daily / 不存在天体 |
| D | 2 | Sentry 优先级:Apophis 100 年风险 / 强制 Öpik |
| E | 5 | 多工具链:Bennu brief / Halley 2061 / HG 表 / H cross-check / Carvano C-complex |

## 故障排查

- **`SystemExit: ASTRO_RESEARCH_FOCUS 必须是 'solar_system'`** — 没 export focus
- **`SystemExit: 缺 ANTHROPIC_API_KEY`** — 没 export key,或在 backend/.env 里写
- **`InferenceError: anthropic ...`** — key 错或网络问题,看具体报错
- **某个 case timeout** — 默认 agent loop 360s,真挂了改 `workflow_budget`
- **MPC/Horizons connector 失败** — 网络问题,或 archive 临时不可用,看 events 里的 error_class
