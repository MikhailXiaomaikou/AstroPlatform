#!/usr/bin/env python3
"""Build the canonical MCP report artifact for the v0.2 evaluation."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCORES = REPO_ROOT / ".local/standard-astro-v02/evaluation_scores.csv"
DEFAULT_SUMMARY = REPO_ROOT / ".local/standard-astro-v02/evaluation_summary.json"
DEFAULT_TASKS = REPO_ROOT / "docs/research/standard_astro_v02_preregistered_tasks.json"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "docs/research/assets/standard_astro_v02_report_artifact.json"
)
TITLE = "Standard Astro v0.2：灵活但可审计的轻量研究验证"
MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
)
CONDITIONS = ("direct", "standard_astro")
DIMENSIONS = (
    "source_traceability",
    "numeric_evidence_constraint",
    "uncertainty_calibration",
    "capability_gap_handling",
    "end_to_end_success",
    "obvious_error_risk",
)
DIMENSION_LABELS = {
    "source_traceability": "来源可追踪性",
    "numeric_evidence_constraint": "数值证据约束",
    "uncertainty_calibration": "不确定性校准",
    "capability_gap_handling": "能力缺口处理",
    "end_to_end_success": "端到端成功",
    "obvious_error_risk": "低明显错误风险",
}
TASK_LABELS = {
    "V02_01": "DESI 距离比",
    "V02_02": "DESI 相关性",
    "V02_03": "ACT H0 参照",
    "V02_04": "ACT n_s 比较",
    "V02_05": "H0 锚点",
    "V02_06": "Pantheon+ 覆盖",
    "V02_07": "EDE 能力缺口",
    "V02_08": "伪证据拒绝",
}
RELEASE_LABELS = {
    "formal_matrix_complete": "192 样本矩阵完整",
    "lightweight_route_accuracy_100pct": "任务路由正确率 100%",
    "expected_answer_hard_block_rate_zero": "应答任务硬拦截率为 0",
    "unverified_numeric_or_attribution_escape_zero": "无依据数字或错误归因逃逸为 0",
    "standard_score_at_least_85pct": "Standard Astro 总分至少 85%",
    "lead_at_least_5_percentage_points": "领先裸模型至少 5 个百分点",
    "source_and_numeric_dimensions_at_least_95pct": "来源与数值两维至少 95%",
    "capability_gap_not_below_direct": "能力缺口题不低于裸模型",
    "lightweight_p95_at_most_60_seconds": "轻量路径 P95 不高于 60 秒",
    "cache_hit_p95_at_most_15_seconds": "缓存命中 P95 不高于 15 秒",
    "desi_core_all_repeats_pass_five_science_checks": "DESI 核心案例五项验收全通过",
}


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("Cannot compute a percentile from an empty sample.")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _read_scores(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 192 or len({row["sample_key"] for row in rows}) != 192:
        raise ValueError("The report artifact requires 192 unique score rows.")
    if {row["model"] for row in rows} != set(MODELS):
        raise ValueError("The score table does not contain the frozen model set.")
    return rows


def _source_specs(generated_at: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "v02-score-audit",
            "label": "Standard Astro v0.2 deterministic score audit",
            "query": {
                "engine": "Python rule audit",
                "language": "python",
                "sql": (
                    "SELECT * FROM read_csv_auto("
                    "'docs/research/assets/standard_astro_v02_scores.csv', "
                    "header = true);"
                ),
                "description": (
                    "Recomputes six frozen 0–2 dimensions from the 192 "
                    "pre-registered user-visible responses and compact receipts."
                ),
                "executed_at": generated_at,
                "tables_used": [
                    "standard_astro_v02_evaluation_samples",
                    "standard_astro_v02_evaluation_scores",
                ],
                "filters": [
                    "four frozen models",
                    "direct and standard_astro conditions",
                    "eight pre-registered tasks",
                    "three repeats per cell",
                ],
                "metric_definitions": [
                    "sample total = sum of six dimensions, each scored 0–2",
                    "condition percentage = condition score / (96 samples × 12)",
                    "dimension attainment = dimension score / (sample count × 2)",
                    "latency P50/P95 use linear interpolation over completed samples",
                ],
            },
        },
        {
            "id": "v02-preregistration",
            "label": "Standard Astro v0.2 pre-registered task specification",
            "query": {
                "engine": "version-controlled JSON",
                "language": "json",
                "description": (
                    "Frozen task prompts, expected routes, expected dispositions, "
                    "scientific targets, and release thresholds."
                ),
                "executed_at": generated_at,
                "tables_used": ["standard_astro_v02_preregistered_tasks"],
                "filters": ["eight tasks and three non-A/B fault injections"],
            },
        },
    ]


def _build_datasets(
    rows: list[dict[str, str]],
    summary: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    overall = []
    for condition in CONDITIONS:
        item = summary["conditions"][condition]
        overall.append(
            {
                "condition": "裸模型" if condition == "direct" else "Standard Astro",
                "condition_id": condition,
                "score": int(item["score"]),
                "maximum": int(item["maximum"]),
                "percentage": float(item["percentage"]),
                "sample_count": int(item["samples"]),
            }
        )

    by_model_raw: defaultdict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    by_task_raw: defaultdict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_model_raw[(row["model"], row["condition"])].append(row)
        by_task_raw[(row["task_id"][:6], row["condition"])].append(row)
    by_model = []
    for model in MODELS:
        condition_percentages: dict[str, float] = {}
        for condition in CONDITIONS:
            subset = by_model_raw[(model, condition)]
            score = sum(int(row["total"]) for row in subset)
            maximum = len(subset) * 12
            percentage = 100 * score / maximum
            condition_percentages[condition] = percentage
            by_model.append(
                {
                    "model": model,
                    "condition": "裸模型" if condition == "direct" else "Standard Astro",
                    "condition_id": condition,
                    "score": score,
                    "maximum": maximum,
                    "percentage": round(percentage, 3),
                    "sample_count": len(subset),
                }
            )
        for item in by_model[-2:]:
            item["standard_minus_direct_pp"] = round(
                condition_percentages["standard_astro"]
                - condition_percentages["direct"],
                3,
            )

    by_task = []
    for task_index, prefix in enumerate(TASK_LABELS, 1):
        task_id = next(task_id for task_id in tasks if task_id.startswith(prefix))
        for condition in CONDITIONS:
            subset = by_task_raw[(prefix, condition)]
            score = sum(int(row["total"]) for row in subset)
            maximum = len(subset) * 12
            by_task.append(
                {
                    "task_index": task_index,
                    "task": TASK_LABELS[prefix],
                    "task_id": task_id,
                    "condition": "裸模型" if condition == "direct" else "Standard Astro",
                    "condition_id": condition,
                    "score": score,
                    "maximum": maximum,
                    "percentage": round(100 * score / maximum, 3),
                    "sample_count": len(subset),
                    "expected_task_kind": tasks[task_id]["expected_task_kind"],
                    "expected_disposition": tasks[task_id]["expected_disposition"],
                }
            )

    dimensions = []
    for dimension in DIMENSIONS:
        for condition in CONDITIONS:
            subset = [row for row in rows if row["condition"] == condition]
            score = sum(int(row[dimension]) for row in subset)
            maximum = len(subset) * 2
            dimensions.append(
                {
                    "dimension": DIMENSION_LABELS[dimension],
                    "dimension_id": dimension,
                    "condition": "裸模型" if condition == "direct" else "Standard Astro",
                    "condition_id": condition,
                    "score": score,
                    "maximum": maximum,
                    "percentage": round(100 * score / maximum, 3),
                    "sample_count": len(subset),
                }
            )

    latency = []
    for task_index, prefix in enumerate(tuple(TASK_LABELS)[:4], 1):
        task_id = next(task_id for task_id in tasks if task_id.startswith(prefix))
        values = [
            float(row["duration_seconds"])
            for row in rows
            if row["condition"] == "standard_astro"
            and row["task_id"].startswith(prefix)
        ]
        for statistic, quantile in (("P50", 0.50), ("P95", 0.95)):
            latency.append(
                {
                    "task_index": task_index,
                    "task": TASK_LABELS[prefix],
                    "task_id": task_id,
                    "statistic": statistic,
                    "seconds": round(_percentile(values, quantile), 3),
                    "sample_count": len(values),
                    "expected_task_kind": tasks[task_id]["expected_task_kind"],
                    "cache_gate_seconds": 15,
                    "lightweight_gate_seconds": 60,
                }
            )

    release_checks = [
        {
            "check": RELEASE_LABELS.get(key, key),
            "check_id": key,
            "passed": bool(value),
            "status": "PASS" if value else "FAIL",
        }
        for key, value in summary["release_checks"].items()
    ]
    return {
        "overall_scores": overall,
        "model_scores": by_model,
        "task_scores": by_task,
        "dimension_scores": dimensions,
        "lightweight_latency": latency,
        "release_checks": release_checks,
    }


def build_artifact(
    rows: list[dict[str, str]],
    summary: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    sources = _source_specs(generated_at)
    datasets = _build_datasets(rows, summary, tasks)
    direct = summary["conditions"]["direct"]
    standard = summary["conditions"]["standard_astro"]
    lead = float(standard["percentage"]) - float(direct["percentage"])
    auto_pass = bool(summary["automated_release_checks_passed"])
    auto_status = "全部通过" if auto_pass else "仍有未通过项"
    source_numeric_dimensions = summary["source_numeric_dimension_percentages"]
    charts = [
        {
            "id": "overall-score",
            "title": "总体审计得分",
            "subtitle": "每个条件 96 个样本，满分 1,152",
            "intent": "comparison",
            "question": "Standard Astro 是否在同一冻结量表上优于裸模型？",
            "rationale": "零基线分类柱图直接比较两个条件的总体达成率。",
            "comparisonContext": {"denominator": "96 × 12", "unit": "%"},
            "type": "bar",
            "dataset": "overall_scores",
            "sourceId": "v02-score-audit",
            "encodings": {
                "x": {"field": "condition", "type": "nominal", "label": "条件"},
                "y": {"field": "percentage", "type": "quantitative", "label": "得分率", "unit": "%"},
                "tooltip": [
                    {"field": "score", "type": "quantitative", "label": "得分"},
                    {"field": "maximum", "type": "quantitative", "label": "满分"},
                    {"field": "sample_count", "type": "quantitative", "label": "样本数"},
                ],
            },
            "valueFormat": "number",
            "unit": "%",
            "palette": {"kind": "categorical", "name": "blue-gold"},
            "settings": {"orientation": "vertical", "groupMode": "single", "showValues": True},
            "referenceLines": [{"axis": "y", "value": 85, "label": "Standard 发布门 85%", "lineStyle": "dashed", "color": "neutral"}],
            "layout": "full",
        },
        {
            "id": "score-by-model",
            "title": "四模型分项得分",
            "subtitle": "每个模型×条件 24 个样本，满分 288",
            "intent": "comparison",
            "question": "系统增益是否只来自某一个基础模型？",
            "rationale": "分组横向条形图适合比较长模型名与两个条件。",
            "comparisonContext": {"grain": "model × condition", "denominator": "24 × 12", "unit": "%"},
            "type": "bar",
            "dataset": "model_scores",
            "sourceId": "v02-score-audit",
            "encodings": {
                "x": {"field": "model", "type": "nominal", "label": "模型"},
                "y": {"field": "percentage", "type": "quantitative", "label": "得分率", "unit": "%"},
                "color": {"field": "condition", "type": "nominal", "label": "条件"},
                "tooltip": [
                    {"field": "score", "type": "quantitative", "label": "得分"},
                    {"field": "standard_minus_direct_pp", "type": "quantitative", "label": "系统增益", "unit": "pp"},
                ],
            },
            "valueFormat": "number",
            "unit": "%",
            "palette": {"kind": "categorical", "name": "blue-gold"},
            "settings": {"orientation": "horizontal", "groupMode": "grouped", "showValues": True},
            "legend": {"position": "bottom", "title": "条件"},
            "layout": "full",
        },
        {
            "id": "task-profile",
            "title": "八道预注册任务的得分剖面",
            "subtitle": "按任务编号排序的分类剖面，不是时间趋势",
            "intent": "comparison",
            "question": "系统在哪些任务类型上增益最大，哪里仍有短板？",
            "rationale": "八个有语义顺序的预注册任务用带标记折线展示条件剖面；标题明确其非时间性质。",
            "comparisonContext": {"grain": "ordered preregistered task category", "denominator": "12 × 12", "unit": "%"},
            "type": "line",
            "dataset": "task_scores",
            "sourceId": "v02-score-audit",
            "encodings": {
                "x": {"field": "task", "type": "ordinal", "label": "预注册任务"},
                "y": {"field": "percentage", "type": "quantitative", "label": "得分率", "unit": "%"},
                "color": {"field": "condition", "type": "nominal", "label": "条件"},
                "tooltip": [
                    {"field": "task_id", "type": "text", "label": "任务 ID"},
                    {"field": "expected_task_kind", "type": "text", "label": "预期路由"},
                    {"field": "expected_disposition", "type": "text", "label": "预期状态"},
                ],
            },
            "valueFormat": "number",
            "unit": "%",
            "palette": {"kind": "categorical", "name": "blue-gold"},
            "settings": {"showPoints": "always", "categoryLabelPolicy": "rotate"},
            "legend": {"position": "bottom", "title": "条件"},
            "layout": "full",
        },
        {
            "id": "dimension-profile",
            "title": "六维审计得分",
            "subtitle": "每维每条件满分 192；分值范围 0–2",
            "intent": "comparison",
            "question": "总体差异由来源、数值、边界还是端到端能力驱动？",
            "rationale": "分组六维柱图保留冻结量表的可比性。",
            "comparisonContext": {"grain": "rubric dimension × condition", "denominator": "96 × 2", "unit": "%"},
            "type": "bar",
            "dataset": "dimension_scores",
            "sourceId": "v02-score-audit",
            "encodings": {
                "x": {"field": "dimension", "type": "nominal", "label": "评分维度"},
                "y": {"field": "percentage", "type": "quantitative", "label": "达成率", "unit": "%"},
                "color": {"field": "condition", "type": "nominal", "label": "条件"},
                "tooltip": [
                    {"field": "score", "type": "quantitative", "label": "维度得分"},
                    {"field": "maximum", "type": "quantitative", "label": "维度满分"},
                ],
            },
            "valueFormat": "number",
            "unit": "%",
            "palette": {"kind": "categorical", "name": "blue-gold"},
            "settings": {"orientation": "vertical", "groupMode": "grouped", "showValues": True, "categoryLabelPolicy": "rotate"},
            "legend": {"position": "bottom", "title": "条件"},
            "layout": "full",
        },
        {
            "id": "lightweight-latency",
            "title": "轻量验证任务延迟",
            "subtitle": "Standard Astro 条件；每题 12 个样本的 P50/P95",
            "intent": "comparison",
            "question": "轻量任务是否达到 60 秒与缓存 15 秒发布门？",
            "rationale": "四个离散轻量任务不构成时间趋势，分组柱图比折线更诚实。",
            "comparisonContext": {"grain": "lightweight task × percentile", "unit": "seconds"},
            "type": "bar",
            "dataset": "lightweight_latency",
            "sourceId": "v02-score-audit",
            "encodings": {
                "x": {"field": "task", "type": "ordinal", "label": "轻量任务"},
                "y": {"field": "seconds", "type": "quantitative", "label": "延迟", "unit": "秒"},
                "color": {"field": "statistic", "type": "nominal", "label": "分位数"},
                "tooltip": [
                    {"field": "sample_count", "type": "quantitative", "label": "样本数"},
                    {"field": "expected_task_kind", "type": "text", "label": "预期路由"},
                ],
            },
            "valueFormat": "number",
            "unit": "秒",
            "palette": {"kind": "categorical", "name": "blue-pink"},
            "settings": {"orientation": "vertical", "groupMode": "grouped", "showValues": True},
            "referenceLines": [
                {"axis": "y", "value": 15, "label": "缓存 P95 门", "lineStyle": "dotted", "color": "neutral"},
                {"axis": "y", "value": 60, "label": "轻量 P95 门", "lineStyle": "dashed", "color": "neutral"},
            ],
            "legend": {"position": "bottom", "title": "分位数"},
            "layout": "full",
        },
    ]
    release_table = {
        "id": "release-checks",
        "title": "自动发布门",
        "subtitle": "自动检查不能替代博士后盲评和 72 小时观察",
        "dataset": "release_checks",
        "defaultSort": {"field": "status", "direction": "asc"},
        "density": "spacious",
        "sourceId": "v02-score-audit",
        "layout": "full",
        "columns": [
            {"field": "check", "label": "检查", "type": "text"},
            {"field": "status", "label": "结果", "type": "text"},
        ],
    }
    blocks = [
        {"id": "title", "type": "markdown", "body": f"# {TITLE}", "layout": "full"},
        {
            "id": "technical-summary",
            "type": "markdown",
            "sourceId": "v02-score-audit",
            "body": (
                "## 技术摘要\n\n"
                f"四模型、两条件、八任务、三次重复共 **192/192** 个样本。"
                f"裸模型得分 **{direct['score']}/{direct['maximum']} ({direct['percentage']:.1f}%)**；"
                f"Standard Astro 得分 **{standard['score']}/{standard['maximum']} ({standard['percentage']:.1f}%)**，"
                f"差值 **{lead:+.1f} 个百分点**。自动发布门：**{auto_status}**。"
                "这证明的是冻结微任务上的可审计性与路由效果，不证明系统已能独立完成论文级研究；"
                "Alpha v0.2 仍需 12 组博士后匿名 A/B 和 72 小时开关观察。"
            ),
            "layout": "full",
        },
        {
            "id": "overall-finding",
            "type": "markdown",
            "sourceId": "v02-score-audit",
            "body": (
                "## 总体差异来自可审计交付，而不是扩大模型自由\n\n"
                "下图按相同 12 分量表比较两个条件。零基线保留绝对差异；"
                "结论只适用于这 96 对条件样本，不能外推为通用模型安全率。"
            ),
            "layout": "full",
        },
        {"id": "overall-chart-block", "type": "chart", "chartId": "overall-score", "layout": "full"},
        {
            "id": "model-finding",
            "type": "markdown",
            "sourceId": "v02-score-audit",
            "body": (
                "## 模型与任务剖面显示增益是否稳健\n\n"
                "逐模型图用于检查总体提升是否由单个模型驱动；任务剖面用于定位"
                "轻量计算、覆盖边界、完整研究缺口和伪证据拒绝之间的差异。"
                "任务折线只是有序分类剖面，不代表时间变化。"
            ),
            "layout": "full",
        },
        {"id": "model-chart-block", "type": "chart", "chartId": "score-by-model", "layout": "full"},
        {"id": "task-chart-block", "type": "chart", "chartId": "task-profile", "layout": "full"},
        {
            "id": "dimension-finding",
            "type": "markdown",
            "sourceId": "v02-score-audit",
            "body": (
                "## 六维得分与延迟共同约束可用性\n\n"
                "来源可追踪性达成率为 "
                f"**{source_numeric_dimensions['source_traceability']:.1f}%**，"
                "数值证据约束为 "
                f"**{source_numeric_dimensions['numeric_evidence_constraint']:.1f}%**；"
                "发布门按两维分别达到 95% 判定，不使用二者平均数。"
                "六维图分解总体分数；延迟图只看前四道确定性轻量任务，并同时显示 P50 与 P95，"
                "避免用平均值掩盖慢尾。"
            ),
            "layout": "full",
        },
        {"id": "dimension-chart-block", "type": "chart", "chartId": "dimension-profile", "layout": "full"},
        {"id": "latency-chart-block", "type": "chart", "chartId": "lightweight-latency", "layout": "full"},
        {
            "id": "scope-definitions",
            "type": "markdown",
            "sourceId": "v02-preregistration",
            "body": (
                "## 评测范围、分母与状态定义\n\n"
                "总体条件分母为 96 个样本×12 分；模型分项为 24×12；单任务条件为 12×12；"
                "单维为 96×2。`full` 表示算术与原文值均核实，`limited` 表示算术有效但来源、"
                "相关性或适用范围有限，`abstention` 表示缺必需输入，`refusal` 表示拒绝伪造证据，"
                "`hard_block` 仅指现有平台门真正扣留不安全草稿。"
            ),
            "layout": "full",
        },
        {
            "id": "methodology",
            "type": "markdown",
            "sourceId": "v02-preregistration",
            "body": (
                "## 实验设计与验证方法\n\n"
                "四个模型分别在闭卷裸模型与 Standard Astro 条件下回答同一题面，temperature=0，"
                "每格重复三次。六维量表在运行前冻结；轻量路径使用受控 operation 与解析 Jacobian，"
                "不执行模型生成代码。来源与派生数值授权分离，用户提供的 source_status 被忽略。"
                "此外运行三类非 A/B 故障注入：抓取超时保留算术、来源冲突禁止论文归因、缓存命中逐字节一致。"
            ),
            "layout": "full",
        },
        {
            "id": "release-section",
            "type": "markdown",
            "sourceId": "v02-score-audit",
            "body": (
                "## 自动发布门只决定是否进入专家测试\n\n"
                f"本轮自动门为 **{auto_status}**。即使全部通过，也只能启用默认关闭的功能开关做专家测试；"
                "它不能替代科学方法适用性审查、真实用户证据或生产恢复验证。"
            ),
            "layout": "full",
        },
        {"id": "release-table-block", "type": "table", "tableId": "release-checks", "layout": "full"},
        {
            "id": "limitations",
            "type": "markdown",
            "body": (
                "## 局限、稳健性与不确定性\n\n"
                "1. 八道任务是高价值微任务，不代表全部观测宇宙学。\n"
                "2. 一阶 Jacobian 不适用于强非线性、非高斯或边界主导问题。\n"
                "3. `verified_exact` 只证明定位窗口中的标签与数值一致，不证明方法适用或论文结论已复现。\n"
                "4. 自动评分由项目方设计，可能存在与专家判断不同的构念效度。\n"
                "5. 来源抓取依赖公开网络和页面结构；付费墙不在 v0.2 范围。"
            ),
            "layout": "full",
        },
        {
            "id": "next-steps",
            "type": "markdown",
            "body": (
                "## 建议的下一步\n\n"
                "1. 把固定抽取的 12 组匿名 A/B 交给博士后，记录逐项修改意见。\n"
                "2. 专家门通过后只对测试用户打开 `LIGHTWEIGHT_VERIFICATION_ENABLED`。\n"
                "3. 连续观察 72 小时的错误路由、来源超时、状态分布与门禁逃逸。\n"
                "4. 任一无依据数字、错误论文归因或旧盲测回归出现时立即关闭开关。"
            ),
            "layout": "full",
        },
        {
            "id": "further-questions",
            "type": "markdown",
            "body": (
                "## 仍需回答的问题\n\n"
                "- 博士后是否认为 `limited` 的方法边界足够具体，还是仍像模板？\n"
                "- 更多论文微任务会暴露哪些符号别名、来源页面和误差模型失败？\n"
                "- 当轻量工具不覆盖新 operation 时，模型探索与受控升级的最佳阈值是什么？"
            ),
            "layout": "full",
        },
    ]
    manifest = {
        "version": 1,
        "surface": "report",
        "title": TITLE,
        "description": "192-sample technical evaluation of Standard Astro v0.2 lightweight verification.",
        "generatedAt": generated_at,
        "sources": sources,
        "charts": charts,
        "tables": [release_table],
        "blocks": blocks,
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": datasets,
        },
        "sources": sources,
        "package_info": {
            "evaluation_id": "standard-astro-v02-lightweight-verification",
            "release_candidate": "Alpha v0.2",
            "expert_review_pending": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = _read_scores(args.scores)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    task_payload = json.loads(args.tasks.read_text(encoding="utf-8"))
    tasks = {str(task["id"]): task for task in task_payload.get("tasks") or []}
    if summary.get("samples") != 192 or len(tasks) != 8:
        raise ValueError("Summary/tasks do not describe the frozen v0.2 matrix.")
    artifact = build_artifact(rows, summary, tasks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote canonical v0.2 report artifact: {args.output}")


if __name__ == "__main__":
    main()
