#!/usr/bin/env python3
"""Render five reproducible figures from the audited v0.2 score CSV."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCORES = REPO_ROOT / ".local/standard-astro-v02/evaluation_scores.csv"
DEFAULT_ASSETS = REPO_ROOT / "docs/research/assets"
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
DIMENSION_LABELS = (
    "Source",
    "Numeric",
    "Uncertainty",
    "Gap handling",
    "End-to-end",
    "Low error risk",
)
TASK_PREFIXES = tuple(f"V02_{index:02d}" for index in range(1, 9))
TASK_LABELS = (
    "DESI ratio",
    "DESI rho",
    "ACT H0",
    "ACT ns",
    "H0 anchors",
    "Pantheon z=12",
    "Full EDE gap",
    "Fake evidence",
)
DIRECT = "#D29A2E"
STANDARD = "#2E6EA6"
INK = "#20242A"
GRID = "#D9DEE5"


def _read_scores(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 192:
        raise ValueError(f"Expected 192 audited samples, found {len(rows)}.")
    keys = {row["sample_key"] for row in rows}
    if len(keys) != len(rows):
        raise ValueError("Score rows contain duplicate sample keys.")
    for row in rows:
        components = [int(row[field]) for field in DIMENSIONS]
        if any(value not in {0, 1, 2} for value in components):
            raise ValueError(f"Score outside frozen 0--2 rubric: {row['sample_key']}")
        if int(row["total"]) != sum(components):
            raise ValueError(f"Invalid total: {row['sample_key']}")
    return rows


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Arial Unicode MS",
                "Noto Sans CJK SC",
                "DejaVu Sans",
            ],
            "font.size": 9.5,
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "text.color": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "svg.fonttype": "none",
        }
    )


def _save(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_dir / f"{stem}.svg",
        bbox_inches="tight",
        metadata={"Creator": "Standard Astro v0.2 figure renderer", "Date": None},
    )
    fig.savefig(
        output_dir / f"{stem}.png",
        bbox_inches="tight",
        dpi=220,
        metadata={"Software": "Standard Astro v0.2 figure renderer"},
    )
    plt.close(fig)


def _condition_totals(rows: list[dict[str, str]]) -> dict[str, int]:
    totals: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        totals[row["condition"]] += int(row["total"])
    return dict(totals)


def render_overall(rows: list[dict[str, str]], output_dir: Path) -> None:
    totals = _condition_totals(rows)
    maximum = 96 * 12
    values = [100 * totals[condition] / maximum for condition in CONDITIONS]
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    bars = ax.bar(
        ("Direct model", "Standard Astro"),
        values,
        color=(DIRECT, STANDARD),
        width=0.58,
    )
    ax.set_ylim(0, 100)
    ax.set_ylabel("Audited score (%)")
    ax.set_title("Overall v0.2 audited score", loc="left", weight="bold")
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    for bar, value, condition in zip(bars, values, CONDITIONS, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 1.5,
            f"{totals[condition]}/{maximum}\n{value:.1f}%",
            ha="center",
            weight="bold",
        )
    _save(fig, output_dir, "standard_astro_v02_overall")


def render_by_model(rows: list[dict[str, str]], output_dir: Path) -> None:
    totals: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        totals[(row["model"], row["condition"])] += int(row["total"])
    maximum = 24 * 12
    y = np.arange(len(MODELS))
    height = 0.34
    fig, ax = plt.subplots(figsize=(9, 5.4))
    for offset, condition, color, label in (
        (-height / 2, "direct", DIRECT, "Direct model"),
        (height / 2, "standard_astro", STANDARD, "Standard Astro"),
    ):
        values = [100 * totals[(model, condition)] / maximum for model in MODELS]
        bars = ax.barh(y + offset, values, height, color=color, label=label)
        for bar, value in zip(bars, values, strict=True):
            ax.text(value + 0.8, bar.get_y() + bar.get_height() / 2, f"{value:.1f}%", va="center")
    ax.set_yticks(y, MODELS)
    ax.invert_yaxis()
    ax.set_xlim(0, 105)
    ax.set_xlabel("Audited score (%)")
    ax.set_title("Score by model and condition", loc="left", weight="bold")
    ax.xaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="lower right")
    _save(fig, output_dir, "standard_astro_v02_by_model")


def render_task_profile(rows: list[dict[str, str]], output_dir: Path) -> None:
    totals: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        prefix = row["task_id"][:6]
        totals[(prefix, row["condition"])] += int(row["total"])
    maximum = 12 * 12
    x = np.arange(len(TASK_PREFIXES))
    fig, ax = plt.subplots(figsize=(10.2, 5.4))
    for condition, color, marker, label in (
        ("direct", DIRECT, "o", "Direct model"),
        ("standard_astro", STANDARD, "s", "Standard Astro"),
    ):
        values = [100 * totals[(prefix, condition)] / maximum for prefix in TASK_PREFIXES]
        ax.plot(x, values, color=color, marker=marker, linewidth=2.2, label=label)
    ax.set_xticks(x, TASK_LABELS, rotation=24, ha="right")
    ax.set_ylim(0, 105)
    ax.set_ylabel("Audited score (%)")
    ax.set_title(
        "Task profile (categorical tasks, not a time trend)",
        loc="left",
        weight="bold",
    )
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    _save(fig, output_dir, "standard_astro_v02_task_profile")


def render_dimensions(rows: list[dict[str, str]], output_dir: Path) -> None:
    totals: defaultdict[tuple[str, str], int] = defaultdict(int)
    counts: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        condition = row["condition"]
        counts[condition] += 1
        for field in DIMENSIONS:
            totals[(condition, field)] += int(row[field])
    x = np.arange(len(DIMENSIONS))
    width = 0.36
    fig, ax = plt.subplots(figsize=(10, 5.4))
    for offset, condition, color, label in (
        (-width / 2, "direct", DIRECT, "Direct model"),
        (width / 2, "standard_astro", STANDARD, "Standard Astro"),
    ):
        values = [
            100 * totals[(condition, field)] / (counts[condition] * 2)
            for field in DIMENSIONS
        ]
        ax.bar(x + offset, values, width, color=color, label=label)
    ax.set_xticks(x, DIMENSION_LABELS, rotation=20, ha="right")
    ax.set_ylim(0, 105)
    ax.set_ylabel("Rubric attainment (%)")
    ax.set_title("Six-dimensional audit profile", loc="left", weight="bold")
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="lower right")
    _save(fig, output_dir, "standard_astro_v02_dimensions")


def _percentile(values: list[float], percentile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), percentile))


def render_latency(rows: list[dict[str, str]], output_dir: Path) -> None:
    standard = [row for row in rows if row["condition"] == "standard_astro"]
    by_task: defaultdict[str, list[float]] = defaultdict(list)
    for row in standard:
        by_task[row["task_id"][:6]].append(float(row["duration_seconds"]))
    p50 = [_percentile(by_task[prefix], 50) for prefix in TASK_PREFIXES]
    p95 = [_percentile(by_task[prefix], 95) for prefix in TASK_PREFIXES]
    x = np.arange(len(TASK_PREFIXES))
    fig, ax = plt.subplots(figsize=(10.2, 5.4))
    ax.plot(x, p50, marker="o", color=STANDARD, linewidth=2.2, label="P50")
    ax.plot(x, p95, marker="^", color="#8B3A62", linewidth=2.0, label="P95")
    ax.axhline(60, color="#B00020", linestyle="--", linewidth=1.2, label="Light-task P95 gate (60s)")
    ax.axhline(15, color="#765600", linestyle=":", linewidth=1.2, label="Cache-hit P95 gate (15s)")
    ax.set_xticks(x, TASK_LABELS, rotation=24, ha="right")
    ax.set_ylabel("Response duration (seconds)")
    ax.set_title("Standard Astro latency by task", loc="left", weight="bold")
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8)
    _save(fig, output_dir, "standard_astro_v02_latency")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ASSETS)
    args = parser.parse_args()
    rows = _read_scores(args.scores)
    _style()
    render_overall(rows, args.output_dir)
    render_by_model(rows, args.output_dir)
    render_task_profile(rows, args.output_dir)
    render_dimensions(rows, args.output_dir)
    render_latency(rows, args.output_dir)
    print(f"Rendered five v0.2 figures from {len(rows)} audited samples.")


if __name__ == "__main__":
    main()
