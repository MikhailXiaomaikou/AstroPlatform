#!/usr/bin/env python3
"""Write the v0.2 evaluation paper from audited, pre-registered artifacts."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCORES = REPO_ROOT / ".local/standard-astro-v02/evaluation_scores.csv"
DEFAULT_SUMMARY = REPO_ROOT / ".local/standard-astro-v02/evaluation_summary.json"
DEFAULT_REPORT = (
    REPO_ROOT / "docs/research/STANDARD_ASTRO_V02_EVALUATION_2026-08-04.zh-CN.md"
)
ASSET_DIR = REPO_ROOT / "docs/research/assets"
MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
    "kimi-k3",
)
CONDITIONS = ("direct", "standard_astro")
REPEATS = 3
TASK_COUNT = 8
SAMPLE_MAXIMUM = 12
EXPECTED_SAMPLES = len(MODELS) * len(CONDITIONS) * TASK_COUNT * REPEATS
CONDITION_SAMPLES = len(MODELS) * TASK_COUNT * REPEATS
MODEL_CONDITION_MAXIMUM = TASK_COUNT * REPEATS * SAMPLE_MAXIMUM
TASK_CONDITION_MAXIMUM = len(MODELS) * REPEATS * SAMPLE_MAXIMUM
TASK_LABELS = {
    "V02_01": "DESI DR2 距离比",
    "V02_02": "DESI 相关性敏感度",
    "V02_03": "ACT DR6 H0 固定参照",
    "V02_04": "ACT DR6 n_s 比较",
    "V02_05": "Planck–SH0ES 锚点",
    "V02_06": "Pantheon+ z=12 覆盖",
    "V02_07": "DESI DR2 EDE 完整后验缺口",
    "V02_08": "伪工具记录拒绝",
}


def _read_scores(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if (
        len(rows) != EXPECTED_SAMPLES
        or len({row["sample_key"] for row in rows}) != EXPECTED_SAMPLES
    ):
        raise ValueError(
            f"The report requires the complete unique {EXPECTED_SAMPLES}-sample matrix."
        )
    return rows


def _pct(score: int, maximum: int) -> str:
    return f"{100 * score / maximum:.1f}%" if maximum else "—"


def _bool_label(value: object) -> str:
    return "通过" if value is True else "未通过"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    rows = _read_scores(args.scores)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    if summary.get("samples") != EXPECTED_SAMPLES:
        raise ValueError(
            f"Evaluation summary does not describe {EXPECTED_SAMPLES} samples."
        )

    by_model: defaultdict[tuple[str, str], int] = defaultdict(int)
    by_task: defaultdict[tuple[str, str], int] = defaultdict(int)
    dispositions: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        by_model[(row["model"], row["condition"])] += int(row["total"])
        by_task[(row["task_id"][:6], row["condition"])] += int(row["total"])
        if row["condition"] == "standard_astro":
            dispositions[row.get("response_disposition") or "missing"] += 1

    direct = summary["conditions"]["direct"]
    standard = summary["conditions"]["standard_astro"]
    lead = float(standard["percentage"]) - float(direct["percentage"])
    release_checks = summary["release_checks"]
    latency = summary["latency_seconds"]
    automated_status = (
        "全部通过" if summary["automated_release_checks_passed"] else "存在未通过项"
    )

    model_rows = "\n".join(
        f"| {model} | {by_model[(model, 'direct')]}/{MODEL_CONDITION_MAXIMUM} "
        f"({_pct(by_model[(model, 'direct')], MODEL_CONDITION_MAXIMUM)}) | "
        f"{by_model[(model, 'standard_astro')]}/{MODEL_CONDITION_MAXIMUM} "
        f"({_pct(by_model[(model, 'standard_astro')], MODEL_CONDITION_MAXIMUM)}) |"
        for model in MODELS
    )
    task_rows = "\n".join(
        f"| {prefix} {TASK_LABELS[prefix]} | {by_task[(prefix, 'direct')]}/{TASK_CONDITION_MAXIMUM} "
        f"({_pct(by_task[(prefix, 'direct')], TASK_CONDITION_MAXIMUM)}) | "
        f"{by_task[(prefix, 'standard_astro')]}/{TASK_CONDITION_MAXIMUM} "
        f"({_pct(by_task[(prefix, 'standard_astro')], TASK_CONDITION_MAXIMUM)}) |"
        for prefix in TASK_LABELS
    )
    release_rows = "\n".join(
        f"| `{name}` | {_bool_label(value)} |"
        for name, value in release_checks.items()
    )
    disposition_text = ", ".join(
        f"`{name}`={count}" for name, count in sorted(dispositions.items())
    )

    report = f"""# Standard Astro v0.2：灵活但可审计的轻量研究验证系统

## 摘要

本研究评估 Standard Astro v0.2 能否在保留模型研究灵活性的同时，把公开论文中的小型、可验证计算从重型 likelihood 工作流中分离。实验在实现前冻结 8 道任务，并对五个模型执行直接回答与 Standard Astro 两种条件、每题三次重复，共 `{EXPECTED_SAMPLES}/{EXPECTED_SAMPLES}` 个预注册样本。自动审计结果为：直接模型 `{direct['score']}/{direct['maximum']}`（{direct['percentage']:.1f}%），Standard Astro `{standard['score']}/{standard['maximum']}`（{standard['percentage']:.1f}%），差值 `{lead:+.1f}` 个百分点。自动发布门状态：**{automated_status}**。最初冻结的四模型 `192` 样本结果仍可单独复核；新增 Kimi K3 构成同题同口径的 48 样本扩展。该结论只适用于本题集和冻结评分规则；博士后 12 组匿名 A/B 复核尚须独立完成，不能以自动评分替代。

## 研究问题

1. 混合路由能否把明确表格计算送入轻量验证，而不因 DESI、BAO、CMB 等领域词误入完整研究矩阵？
2. 确定性工具能否同时保持数值、误差传播、单位、相关性和来源归因的可审计性？
3. 当来源超时、冲突或缺少跨数据集协方差时，系统能否保留合法算术并准确降级？
4. 相比裸模型，系统能否降低无依据数字和错误论文归因，同时不削弱能力缺口说明？

## 系统设计

v0.2 统一输出 `deterministic_source_check`、`research_exploration`、`full_research` 和 `general` 四种任务类型。高置信度轻量任务绕过模型生成代码，使用受控的 ratio、difference、product 或 generalized inverse-covariance weighted mean，并以解析 Jacobian 传播一阶不确定性。来源解析器支持 arXiv/ar5iv、arXiv 源/PDF、DOI 公开页面/PDF、Zenodo 官方附件、HTTPS URL 与带哈希缓存。来源匹配与派生数值分别授权，计算正确不会自动升级为“论文报告了该值”。

![总体评分](./assets/standard_astro_v02_overall.svg)

## 实验设计与评分

- 模型：`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`claude-fable-5`。
- 条件：裸模型闭卷回答；Standard Astro 真实工具与门禁路径。
- 任务：8 道预注册观测宇宙学任务，每个实验单元重复 3 次。
- 评分：来源可追踪性、数值证据约束、不确定性校准、能力缺口处理、端到端成功、明显错误风险六维，每维 0–2 分，总分 12。
- 原始回答保存在忽略版本控制的 `.local/standard-astro-v02/evaluation_samples.jsonl`；仓库只保存可重算评分、汇总和图表。
- 自动规则审计不是专家评审；每条得分都保留维度值与异常标记，供复核者质疑。

## 结果

### 总体与模型

| 模型 | 直接条件 | Standard Astro |
|---|---:|---:|
{model_rows}

![逐模型评分](./assets/standard_astro_v02_by_model.svg)

### 分任务结果

| 任务 | 直接条件 | Standard Astro |
|---|---:|---:|
{task_rows}

![任务剖面（非时间趋势）](./assets/standard_astro_v02_task_profile.svg)

### 六维审计与状态

Standard Astro 的来源与数值证据两维联合达成率为 `{summary['source_numeric_percentage']:.1f}%`。{CONDITION_SAMPLES} 个 Standard Astro 样本的终态构成为：{disposition_text}。

![六维评分](./assets/standard_astro_v02_dimensions.svg)

### 延迟

- 轻量路径 P50：`{latency['lightweight_p50']:.3f}` 秒；P95：`{latency['lightweight_p95']:.3f}` 秒。
- 缓存命中 P50：`{latency['cache_hit_p50']:.3f}` 秒；P95：`{latency['cache_hit_p95']:.3f}` 秒。

![任务延迟](./assets/standard_astro_v02_latency.svg)

## 预注册发布门

| 自动检查 | 结果 |
|---|---|
{release_rows}

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

v0.2 的判断标准不是“模型是否显得更聪明”，而是小型研究核查能否更快进入正确路径、留下可验算凭证，并在证据不足时给出有用而准确的边界。是否标记 Alpha v0.2 取决于上述自动门与独立专家门共同通过。

## 复现

```bash
cd backend
OPENAI_CLI_ENABLED=1 OPENAI_CLI_COMMAND=codex \\
CLAUDE_CLI_ENABLED=1 CLAUDE_CLI_COMMAND=claude \\
./venv/bin/python -m scripts.evaluate_standard_astro_v02
./venv/bin/python -m scripts.score_standard_astro_v02
MPLCONFIGDIR=/tmp/standard-astro-mpl \\
./venv/bin/python -m scripts.render_standard_astro_v02_figures
./venv/bin/python -m scripts.build_standard_astro_v02_expert_pack
```
"""

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.scores, ASSET_DIR / "standard_astro_v02_scores.csv")
    shutil.copyfile(args.summary, ASSET_DIR / "standard_astro_v02_summary.json")
    print(f"Wrote v0.2 report and audited assets: {args.report}")


if __name__ == "__main__":
    main()
