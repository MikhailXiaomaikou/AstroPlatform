#!/usr/bin/env python3
"""Validate v0.2 paper aggregates, local figures, and blinded expert pack."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = (
    REPO_ROOT / "docs/research/STANDARD_ASTRO_V02_EVALUATION_2026-08-04.zh-CN.md"
)
DEFAULT_SCORES = REPO_ROOT / "docs/research/assets/standard_astro_v02_scores.csv"
DEFAULT_SUMMARY = REPO_ROOT / "docs/research/assets/standard_astro_v02_summary.json"
DEFAULT_PACK = REPO_ROOT / ".local/standard-astro-v02/expert_blind_pack.zh-CN.md"
DEFAULT_KEY = REPO_ROOT / ".local/standard-astro-v02/expert_blind_answer_key.json"
REQUIRED_HEADINGS = (
    "## 摘要",
    "## 研究问题",
    "## 系统设计",
    "## 实验设计与评分",
    "## 结果",
    "## 预注册发布门",
    "## 专家盲测",
    "## 局限性",
    "## 结论",
    "## 复现",
)
EXPECTED_FIGURES = {
    "./assets/standard_astro_v02_overall.svg",
    "./assets/standard_astro_v02_by_model.svg",
    "./assets/standard_astro_v02_task_profile.svg",
    "./assets/standard_astro_v02_dimensions.svg",
    "./assets/standard_astro_v02_latency.svg",
}
DIMENSIONS = (
    "source_traceability",
    "numeric_evidence_constraint",
    "uncertainty_calibration",
    "capability_gap_handling",
    "end_to_end_success",
    "obvious_error_risk",
)


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Invalid PNG signature: {path}")
    return struct.unpack(">II", header[16:24])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--expert-pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--answer-key", type=Path, default=DEFAULT_KEY)
    args = parser.parse_args()

    report = args.report.read_text(encoding="utf-8")
    for heading in REQUIRED_HEADINGS:
        if heading not in report:
            raise ValueError(f"Missing paper section: {heading}")

    with args.scores.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 192 or len({row["sample_key"] for row in rows}) != 192:
        raise ValueError("Audited score table is not the unique 192-sample matrix.")
    totals: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        components = [int(row[field]) for field in DIMENSIONS]
        if int(row["total"]) != sum(components):
            raise ValueError(f"Score components do not sum: {row['sample_key']}")
        totals[row["condition"]] += int(row["total"])

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    for condition in ("direct", "standard_astro"):
        if totals[condition] != summary["conditions"][condition]["score"]:
            raise ValueError(f"Report summary drift for {condition}.")
        score_claim = (
            f"`{totals[condition]}/{summary['conditions'][condition]['maximum']}`"
        )
        if score_claim not in report:
            raise ValueError(f"Paper is missing aggregate claim: {score_claim}")

    links = set(re.findall(r"!\[[^]]*]\(([^)]+)\)", report))
    if links != EXPECTED_FIGURES:
        raise ValueError(f"Unexpected report figure links: {sorted(links)}")
    asset_root = args.report.parent / "assets"
    for link in links:
        svg = (args.report.parent / link).resolve()
        if asset_root.resolve() not in svg.parents or not svg.is_file():
            raise ValueError(f"Missing or unsafe SVG: {link}")
        root = ET.parse(svg).getroot()
        if not root.tag.endswith("svg") or not root.get("viewBox"):
            raise ValueError(f"SVG lacks a viewBox: {svg}")
        for element in root.iter():
            for key, value in element.attrib.items():
                if key.endswith("href") and value.startswith(("http://", "https://")):
                    raise ValueError(f"SVG contains an external resource: {svg}")
        width, height = _png_dimensions(svg.with_suffix(".png"))
        if width < 1200 or height < 800:
            raise ValueError(f"PNG is too small: {svg.with_suffix('.png')}")

    pack = args.expert_pack.read_text(encoding="utf-8")
    key = json.loads(args.answer_key.read_text(encoding="utf-8"))
    if len(re.findall(r"^## PAIR-\d{2}$", pack, re.M)) != 12:
        raise ValueError("Expert pack does not contain 12 blinded pairs.")
    if len(key.get("pairs") or []) != 12:
        raise ValueError("Expert answer key does not contain 12 pairs.")
    for forbidden in (
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "claude-fable-5",
        "standard_astro",
        "condition",
    ):
        if forbidden in pack:
            raise ValueError(f"Expert pack leaks blinded metadata: {forbidden}")

    print(
        "v0.2 report validation passed: 192 scores, 10 sections, "
        "5 local figures, and 12 blinded expert pairs"
    )


if __name__ == "__main__":
    main()
