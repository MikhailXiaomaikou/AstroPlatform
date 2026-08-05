#!/usr/bin/env python3
"""Validate the generated Standard Astro v0.2 formal report package."""

from __future__ import annotations

import csv
from pathlib import Path

from docx import Document


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = REPO_ROOT / "docs/research/formal_report_package_2026-08-05"
SCORES = REPORT_ROOT / "evidence/standard_astro_v02_scores_240.csv"


def document_text(path: Path) -> str:
    doc = Document(path)
    parts = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def main() -> None:
    docx_files = sorted(REPORT_ROOT.rglob("*.docx"))
    assert len(docx_files) == 10, f"expected 10 DOCX files, found {len(docx_files)}"

    with SCORES.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 240, f"expected 240 score rows, found {len(rows)}"

    technical = document_text(REPORT_ROOT / "01_Standard_Astro_v0.2_总体技术报告.docx")
    for expected in (
        "总体技术报告",
        "deterministic_source_check",
        "research_exploration",
        "full_research",
        "general",
        "1440/1440",
        "240/240",
        "博士后 12 组匿名 A/B 盲评尚未完成",
    ):
        assert expected in technical, f"technical report missing {expected!r}"

    summary = document_text(REPORT_ROOT / "02_Standard_Astro_v0.2_测试结果综述.docx")
    for expected in (
        "839/1440",
        "58.3%",
        "1440/1440",
        "100.0%",
        "240/240",
        "Kimi K3",
        "安全概率",
    ):
        assert expected in summary, f"evaluation summary missing {expected!r}"

    experiment_docs = sorted((REPORT_ROOT / "03_逐实验报告").glob("*.docx"))
    assert len(experiment_docs) == 8
    for index, path in enumerate(experiment_docs, 1):
        text = document_text(path)
        for expected in (
            f"实验 {index}",
            "科学背景与研究问题",
            "预注册验收标准",
            "测试结果",
            "系统行为审计",
            "结论范围与局限",
            "English abstract",
        ):
            assert expected in text, f"{path.name} missing {expected!r}"

    required_evidence = {
        "standard_astro_v02_preregistered_tasks.json",
        "standard_astro_v02_scores_240.csv",
        "standard_astro_v02_summary.json",
        "strict_blind_test_summary.md",
    }
    actual_evidence = {path.name for path in (REPORT_ROOT / "evidence").iterdir()}
    assert required_evidence <= actual_evidence

    print(
        "PASS: 10 DOCX reports, 240 score rows, key findings, 8 experiment sections, "
        "and 4 evidence artifacts validated."
    )


if __name__ == "__main__":
    main()
