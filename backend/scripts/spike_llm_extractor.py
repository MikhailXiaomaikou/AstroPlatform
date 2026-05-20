"""Stage 6.3 spike CLI runner (2026-05-20).

跑法:
  cd backend
  ANTHROPIC_API_KEY=sk-ant-... python scripts/spike_llm_extractor.py [arxiv_id]

默认跑 REBELS Bouwens+2022 (2106.13719). 该论文 overview 表里多半没完整
FWHM/L[CII], 用来测 "LLM 该返回 null 时是否真的返 null + 反查给 pass"
(而不是编数字让反查 fail).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 让 spike 可以从 repo 根目录或 backend/ 目录直接跑
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from app.services.llm_paper_extractor import extract_with_llm_and_verify


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: set ANTHROPIC_API_KEY env var (this is a paid LLM call)")
        return 1

    arxiv_id = sys.argv[1] if len(sys.argv) > 1 else "2106.13719"
    fields = ["source_name", "fwhm_km_s", "log_luminosity", "z"]

    print(f"--- spike: arxiv:{arxiv_id} ---")
    print(f"requesting fields: {fields}")
    print()

    results = extract_with_llm_and_verify(
        arxiv_id=arxiv_id,
        fields=fields,
        api_key=api_key,
    )

    passed = sum(1 for r in results if r.validation_status == "passed")
    failed_mismatch = sum(1 for r in results if r.validation_status == "failed_mismatch")
    failed_no_cell = sum(1 for r in results if r.validation_status == "failed_no_cell")

    print(f"results: {len(results)} total, {passed} passed, "
          f"{failed_mismatch} failed_mismatch, {failed_no_cell} failed_no_cell")
    print()
    for i, r in enumerate(results[:20]):
        print(f"[{i}] {r.validation_status}: {r.source_name} "
              f"FWHM={r.fwhm_km_s} logL={r.log_luminosity} z={r.z} "
              f"(table {r.table_idx} row {r.row_idx})")
        for note in r.validation_notes:
            print(f"    - {note}")
        if r.cell_provenance:
            print(f"    cells: {json.dumps(r.cell_provenance, ensure_ascii=False)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
