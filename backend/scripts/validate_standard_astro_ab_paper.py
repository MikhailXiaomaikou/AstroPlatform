#!/usr/bin/env python3
"""Validate the paper's score claims, local figure links, and static assets."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT = REPO_ROOT / "docs" / "research" / "STANDARD_ASTRO_AB_EVALUATION_2026-08-03.zh-CN.md"
ASSET_DIR = REPORT.parent / "assets"
SCORES = ASSET_DIR / "standard_astro_ab_scores.csv"

SCORE_FIELDS = (
    "source_traceability",
    "numeric_evidence",
    "uncertainty_calibration",
    "capability_gap",
    "e2e_success",
    "low_error_risk",
)
REQUIRED_HEADINGS = (
    "## 摘要",
    "## 研究问题",
    "## 实验设计与方法",
    "## 结果",
    "## 讨论：证据门禁降低漂移，但安全降级仍不够有用",
    "## 后续工作与开放问题",
    "## 局限性与不确定性",
    "## 结论",
    "## 复现实验",
)


def _load_scores() -> list[dict[str, str]]:
    with SCORES.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 24:
        raise ValueError(f"expected 24 score rows, found {len(rows)}")
    return rows


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"invalid PNG signature: {path}")
    return struct.unpack(">II", header[16:24])


def main() -> None:
    report = REPORT.read_text(encoding="utf-8")
    rows = _load_scores()

    for heading in REQUIRED_HEADINGS:
        if heading not in report:
            raise ValueError(f"missing required paper section: {heading}")

    overall: defaultdict[str, int] = defaultdict(int)
    by_model: defaultdict[tuple[str, str], int] = defaultdict(int)
    by_case: defaultdict[tuple[str, str], int] = defaultdict(int)
    by_dimension: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        total = int(row["total"])
        if total != sum(int(row[field]) for field in SCORE_FIELDS):
            raise ValueError(f"invalid score total: {row}")
        condition = row["condition"]
        overall[condition] += total
        by_model[(row["model"], condition)] += total
        by_case[(row["case_id"], condition)] += total
        for field in SCORE_FIELDS:
            by_dimension[(field, condition)] += int(row[field])

    if dict(overall) != {"direct": 106, "standard_astro": 124}:
        raise ValueError(f"unexpected overall scores: {dict(overall)}")

    required_claims = (
        "106/144（73.6%）",
        "124/144（86.1%）",
        "绝对增加 18 分、标准化增加 12.5 个百分点",
        "34/48（70.8%） | 48/48（100.0%）",
        "34/48（70.8%） | 44/48（91.7%）",
        "38/48（79.2%） | 32/48（66.7%）",
    )
    for claim in required_claims:
        if claim not in report:
            raise ValueError(f"report is missing verified claim: {claim}")
    if "任务剖面，非时间趋势" not in report:
        raise ValueError("task-profile line chart is missing its non-time-trend warning")

    expected_by_model = {
        ("gpt-5.6-sol", "direct"): 29,
        ("gpt-5.6-sol", "standard_astro"): 31,
        ("gpt-5.6-terra", "direct"): 29,
        ("gpt-5.6-terra", "standard_astro"): 31,
        ("gpt-5.6-luna", "direct"): 26,
        ("gpt-5.6-luna", "standard_astro"): 31,
        ("claude-fable-5", "direct"): 22,
        ("claude-fable-5", "standard_astro"): 31,
    }
    if dict(by_model) != expected_by_model:
        raise ValueError("per-model aggregates differ from the completed evaluation")

    expected_by_case = {
        ("A2_hubble_tension", "direct"): 34,
        ("A2_hubble_tension", "standard_astro"): 48,
        ("B1_desi_dr1_ap", "direct"): 34,
        ("B1_desi_dr1_ap", "standard_astro"): 44,
        ("C1_full_ede_gap", "direct"): 38,
        ("C1_full_ede_gap", "standard_astro"): 32,
    }
    if dict(by_case) != expected_by_case:
        raise ValueError("per-task aggregates differ from the score table")

    if sum(by_dimension.values()) != 230:
        raise ValueError("dimension aggregates do not sum to both condition totals")

    image_links = re.findall(r"!\[[^]]*]\(([^)]+)\)", report)
    expected_links = {
        "./assets/standard_astro_ab_overall.svg",
        "./assets/standard_astro_ab_by_model.svg",
        "./assets/standard_astro_ab_by_task.svg",
        "./assets/standard_astro_ab_task_profile.svg",
        "./assets/standard_astro_ab_model_dimensions.svg",
    }
    if set(image_links) != expected_links:
        raise ValueError(f"unexpected report image links: {image_links}")

    for relative_link in image_links:
        target = (REPORT.parent / relative_link).resolve()
        if not target.is_file() or ASSET_DIR.resolve() not in target.parents:
            raise ValueError(f"missing or unsafe image target: {relative_link}")
        root = ET.parse(target).getroot()
        if not root.tag.endswith("svg") or not root.get("viewBox"):
            raise ValueError(f"SVG lacks a root viewBox: {target}")
        for element in root.iter():
            for key, value in element.attrib.items():
                if key.endswith("href") and value.startswith(("http://", "https://")):
                    raise ValueError(f"SVG has an external resource: {target}")

        png = target.with_suffix(".png")
        width, height = _png_dimensions(png)
        if width < 1200 or height < 800:
            raise ValueError(f"PNG preview is too small: {png} ({width}×{height})")

    print(
        "paper validation passed: 24 score rows, verified aggregates, "
        "9 required sections, 5 local SVG links, 5 readable PNG previews"
    )


if __name__ == "__main__":
    main()
