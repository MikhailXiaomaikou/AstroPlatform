#!/usr/bin/env python3
"""Validate the audited A/B score table and render the paper figures."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = REPO_ROOT / "docs" / "research" / "assets"
SCORE_PATH = ASSET_DIR / "standard_astro_ab_scores.csv"

MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
)
CONDITIONS = ("direct", "standard_astro")
CASES = (
    "A2_hubble_tension",
    "B1_desi_dr1_ap",
    "C1_full_ede_gap",
)
CASE_LABELS = ("A2 H0 anchors", "B1 DESI DR1 AP", "C1 full EDE gap")
SCORE_FIELDS = (
    "source_traceability",
    "numeric_evidence",
    "uncertainty_calibration",
    "capability_gap",
    "e2e_success",
    "low_error_risk",
)
EXPECTED_OVERALL = {"direct": 106, "standard_astro": 124}
EXPECTED_BY_MODEL = {
    "gpt-5.6-sol": {"direct": 29, "standard_astro": 31},
    "gpt-5.6-terra": {"direct": 29, "standard_astro": 31},
    "gpt-5.6-luna": {"direct": 26, "standard_astro": 31},
    "claude-fable-5": {"direct": 22, "standard_astro": 31},
}

INK = "#20242A"
GRID = "#D9DEE5"
DIRECT = "#D29A2E"
DIRECT_EDGE = "#7A5816"
STANDARD = "#2E6EA6"
STANDARD_EDGE = "#173E61"
DIRECT_OPEN = "#F6E7BF"
STANDARD_OPEN = "#DCEAF5"


def _read_and_validate_scores() -> list[dict[str, str]]:
    with SCORE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if len(rows) != 24:
        raise ValueError(f"expected 24 score rows, found {len(rows)}")

    expected_keys = {
        (model, condition, case_id)
        for model in MODELS
        for condition in CONDITIONS
        for case_id in ("A2_hubble_tension", "B1_desi_dr1_ap", "C1_full_ede_gap")
    }
    actual_keys = {
        (row["model"], row["condition"], row["case_id"]) for row in rows
    }
    if actual_keys != expected_keys:
        raise ValueError("score rows do not cover the fixed 4 × 2 × 3 matrix")

    overall: defaultdict[str, int] = defaultdict(int)
    by_model: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for row in rows:
        components = [int(row[field]) for field in SCORE_FIELDS]
        if any(component < 0 or component > 2 for component in components):
            raise ValueError(f"score outside 0–2 rubric: {row}")
        total = int(row["total"])
        if total != sum(components) or int(row["max_score"]) != 12:
            raise ValueError(f"row total does not match rubric components: {row}")
        if row["run_status"] != "completed" or int(row["sample_count"]) != 1:
            raise ValueError(f"row is not one completed formal sample: {row}")
        overall[row["condition"]] += total
        by_model[row["model"]][row["condition"]] += total

    if dict(overall) != EXPECTED_OVERALL:
        raise ValueError(f"overall scores changed: {dict(overall)}")
    if {model: dict(scores) for model, scores in by_model.items()} != EXPECTED_BY_MODEL:
        raise ValueError("per-model scores changed")
    return rows


def _base_style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial Unicode MS", "Noto Sans CJK SC", "DejaVu Sans"],
        "font.size": 10,
        "axes.edgecolor": INK,
        "axes.labelcolor": INK,
        "axes.titlecolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "text.color": INK,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "svg.fonttype": "none",
    })


def _save(fig: plt.Figure, stem: str) -> None:
    fig.savefig(
        ASSET_DIR / f"{stem}.svg",
        bbox_inches="tight",
        metadata={"Creator": "Standard Astro reproducible figure renderer", "Date": None},
    )
    fig.savefig(
        ASSET_DIR / f"{stem}.png",
        bbox_inches="tight",
        dpi=220,
        metadata={"Software": "Standard Astro reproducible figure renderer"},
    )
    plt.close(fig)


def _aggregate_by_task(rows: list[dict[str, str]]) -> dict[tuple[str, str], int]:
    totals: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        totals[(row["case_id"], row["condition"])] += int(row["total"])
    return dict(totals)


def _aggregate_model_dimensions(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str, str], int]:
    totals: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    for row in rows:
        for field in SCORE_FIELDS:
            totals[(row["model"], row["condition"], field)] += int(row[field])
    return dict(totals)


def render_overall() -> None:
    labels = ("Direct model", "Standard Astro")
    scores = (EXPECTED_OVERALL["direct"], EXPECTED_OVERALL["standard_astro"])
    colors = (DIRECT, STANDARD)
    edges = (DIRECT_EDGE, STANDARD_EDGE)

    fig, ax = plt.subplots(figsize=(7.4, 4.7))
    fig.subplots_adjust(left=0.12, right=0.96, top=0.78, bottom=0.15)
    bars = ax.bar(labels, scores, width=0.56, color=colors, edgecolor=edges, linewidth=1.2)
    ax.set_ylim(0, 150)
    ax.set_ylabel("Audited score (maximum 144)")
    fig.suptitle(
        "Overall audited score by research condition",
        x=0.12,
        y=0.95,
        ha="left",
        weight="bold",
    )
    fig.text(
        0.12,
        0.865,
        "Four models × three tasks × six rubric dimensions; 24 completed responses",
        fontsize=9,
        color="#59616B",
    )
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    for bar, score in zip(bars, scores, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            score + 4,
            f"{score} / 144\n{score / 144:.1%}",
            ha="center",
            va="bottom",
            weight="bold",
        )
    ax.text(
        0.5,
        0.56,
        "+18 points / +12.5 percentage points",
        transform=ax.transAxes,
        ha="center",
        fontsize=10,
        weight="bold",
        color=STANDARD_EDGE,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": GRID},
    )
    _save(fig, "standard_astro_ab_overall")


def render_by_model() -> None:
    y = np.arange(len(MODELS))
    height = 0.34
    direct_scores = [EXPECTED_BY_MODEL[model]["direct"] for model in MODELS]
    standard_scores = [EXPECTED_BY_MODEL[model]["standard_astro"] for model in MODELS]

    fig, ax = plt.subplots(figsize=(8.7, 5.5))
    fig.subplots_adjust(left=0.24, right=0.95, top=0.78, bottom=0.2)
    direct_bars = ax.barh(
        y - height / 2,
        direct_scores,
        height,
        label="Direct model",
        color=DIRECT_OPEN,
        edgecolor=DIRECT_EDGE,
        linewidth=1.2,
        hatch="///",
    )
    standard_bars = ax.barh(
        y + height / 2,
        standard_scores,
        height,
        label="Standard Astro",
        color=STANDARD,
        edgecolor=STANDARD_EDGE,
        linewidth=1.2,
    )
    ax.set_yticks(y, MODELS)
    ax.invert_yaxis()
    ax.set_xlim(0, 36)
    ax.set_xlabel("Audited score per model (maximum 36)")
    fig.suptitle(
        "Audited score by model and research condition",
        x=0.24,
        y=0.95,
        ha="left",
        weight="bold",
    )
    fig.text(
        0.24,
        0.865,
        "Three fixed tasks per model; one formal run per model × condition × task",
        fontsize=9,
        color="#59616B",
    )
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.legend(
        handles=(direct_bars, standard_bars),
        labels=("Direct model", "Standard Astro"),
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.6, 0.015),
        ncol=2,
    )
    for bars in (direct_bars, standard_bars):
        for bar in bars:
            score = int(bar.get_width())
            ax.text(
                score + 0.45,
                bar.get_y() + bar.get_height() / 2,
                f"{score}/36",
                va="center",
                fontsize=9,
                weight="bold",
            )
    _save(fig, "standard_astro_ab_by_model")


def render_by_task(rows: list[dict[str, str]]) -> None:
    totals = _aggregate_by_task(rows)
    x = np.arange(len(CASES))
    width = 0.34
    direct_scores = [totals[(case_id, "direct")] for case_id in CASES]
    standard_scores = [totals[(case_id, "standard_astro")] for case_id in CASES]

    fig, ax = plt.subplots(figsize=(8.7, 5.4))
    fig.subplots_adjust(left=0.11, right=0.96, top=0.78, bottom=0.22)
    direct_bars = ax.bar(
        x - width / 2,
        direct_scores,
        width,
        label="Direct model",
        color=DIRECT_OPEN,
        edgecolor=DIRECT_EDGE,
        linewidth=1.2,
        hatch="///",
    )
    standard_bars = ax.bar(
        x + width / 2,
        standard_scores,
        width,
        label="Standard Astro",
        color=STANDARD,
        edgecolor=STANDARD_EDGE,
        linewidth=1.2,
    )
    ax.set_xticks(x, CASE_LABELS)
    ax.set_ylim(0, 52)
    ax.set_ylabel("Audited score across four models (maximum 48)")
    fig.suptitle(
        "Audited score by fixed research task",
        x=0.11,
        y=0.95,
        ha="left",
        weight="bold",
    )
    fig.text(
        0.11,
        0.865,
        "One completed response per model × condition × task; descriptive totals only",
        fontsize=9,
        color="#59616B",
    )
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper right")
    for bars in (direct_bars, standard_bars):
        for bar in bars:
            score = int(bar.get_height())
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                score + 0.8,
                f"{score}/48",
                ha="center",
                va="bottom",
                fontsize=9,
                weight="bold",
            )
    _save(fig, "standard_astro_ab_by_task")


def render_model_dimensions(rows: list[dict[str, str]]) -> None:
    totals = _aggregate_model_dimensions(rows)
    labels = (
        "Source",
        "Numeric",
        "Uncertainty",
        "Gap handling",
        "E2E",
        "Low error risk",
    )
    direct_map = LinearSegmentedColormap.from_list(
        "standard_astro_direct", ("#FFFDF7", DIRECT)
    )
    standard_map = LinearSegmentedColormap.from_list(
        "standard_astro_system", ("#F7FBFE", STANDARD)
    )

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 5.6), sharey=True)
    fig.subplots_adjust(left=0.16, right=0.96, top=0.76, bottom=0.25, wspace=0.10)
    images = []
    for ax, condition, title, cmap in zip(
        axes,
        CONDITIONS,
        ("Direct model", "Standard Astro"),
        (direct_map, standard_map),
        strict=True,
    ):
        matrix = np.array(
            [
                [totals[(model, condition, field)] for field in SCORE_FIELDS]
                for model in MODELS
            ]
        )
        image = ax.imshow(matrix, vmin=0, vmax=6, cmap=cmap, aspect="auto")
        images.append(image)
        ax.set_title(title, weight="bold", pad=10)
        ax.set_xticks(np.arange(len(labels)), labels, rotation=32, ha="right")
        ax.set_yticks(np.arange(len(MODELS)), MODELS)
        ax.tick_params(length=0)
        for row_idx in range(matrix.shape[0]):
            for col_idx in range(matrix.shape[1]):
                value = int(matrix[row_idx, col_idx])
                ax.text(
                    col_idx,
                    row_idx,
                    str(value),
                    ha="center",
                    va="center",
                    color="white" if value >= 5 else INK,
                    weight="bold",
                )
    fig.suptitle(
        "Per-model component scores by research condition",
        x=0.16,
        y=0.95,
        ha="left",
        weight="bold",
    )
    fig.text(
        0.16,
        0.855,
        "Each cell sums three fixed tasks; 0–6 points. Exact labels preserve grayscale readability.",
        fontsize=9,
        color="#59616B",
    )
    colorbar = fig.colorbar(images[-1], ax=axes, location="right", fraction=0.028, pad=0.03)
    colorbar.set_label("Component score (maximum 6)")
    _save(fig, "standard_astro_ab_model_dimensions")


def render_task_profile(rows: list[dict[str, str]]) -> None:
    totals = _aggregate_by_task(rows)
    x = np.arange(len(CASES))
    direct_scores = [totals[(case_id, "direct")] for case_id in CASES]
    standard_scores = [totals[(case_id, "standard_astro")] for case_id in CASES]

    fig, ax = plt.subplots(figsize=(8.7, 5.4))
    fig.subplots_adjust(left=0.11, right=0.96, top=0.76, bottom=0.25)
    ax.plot(
        x,
        direct_scores,
        color=DIRECT_EDGE,
        linewidth=2.0,
        linestyle="--",
        marker="o",
        markersize=8,
        markerfacecolor="white",
        markeredgewidth=1.6,
        label="Direct model",
    )
    ax.plot(
        x,
        standard_scores,
        color=STANDARD,
        linewidth=2.2,
        marker="s",
        markersize=7.5,
        markerfacecolor=STANDARD_OPEN,
        markeredgecolor=STANDARD_EDGE,
        markeredgewidth=1.4,
        label="Standard Astro",
    )
    ax.set_xticks(x, CASE_LABELS)
    ax.set_xlim(-0.12, len(CASES) - 0.88)
    ax.set_ylim(0, 52)
    ax.set_xlabel("任务剖面，非时间趋势 / Task profile, not a time trend", labelpad=12)
    ax.set_ylabel("Audited score across four models (maximum 48)")
    fig.suptitle(
        "Task profile across three fixed research tasks",
        x=0.11,
        y=0.95,
        ha="left",
        weight="bold",
    )
    fig.text(
        0.11,
        0.845,
        "Lines connect discrete task categories only; do not infer temporal or statistical trend.",
        fontsize=9,
        color="#59616B",
    )
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="lower left")
    for series, color in ((direct_scores, DIRECT_EDGE), (standard_scores, STANDARD_EDGE)):
        for idx, score in enumerate(series):
            ax.text(
                idx,
                score + 1.0,
                f"{score}/48",
                ha="center",
                va="bottom",
                fontsize=9,
                weight="bold",
                color=color,
            )
    _save(fig, "standard_astro_ab_task_profile")


def main() -> None:
    rows = _read_and_validate_scores()
    _base_style()
    render_overall()
    render_by_model()
    render_by_task(rows)
    render_model_dimensions(rows)
    render_task_profile(rows)
    print("validated 24 rows; rendered 5 figures in SVG and PNG")


if __name__ == "__main__":
    main()
