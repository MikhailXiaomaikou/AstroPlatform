from __future__ import annotations

import json
from pathlib import Path


def test_maintenance_report_classifies_local_diagnostic_bundle(tmp_path: Path):
    from app.services.maintenance import build_maintenance_report, render_markdown_report

    diag_dir = tmp_path / "diagnostics"
    diag_dir.mkdir()
    bundle = {
        "schema_version": 1,
        "bundle_id": "bundle-r2",
        "created_at": "2026-04-27T00:00:00+00:00",
        "prompt": "fit [CII] relation",
        "tool_timeline": [
            {
                "name": "extract_literature_tables",
                "result": {
                    "__tool_status__": "COMPLETED",
                    "line_measurement_count": 74,
                },
            },
            {
                "name": "run_python",
                "result": {"__tool_status__": "SYNTHETIC"},
            },
            {
                "name": "fit_line_lfr",
                "result": {
                    "__tool_status__": "FAILED",
                    "error_class": "missing_measurement_cache",
                },
            },
        ],
        "final_reply": "",
    }
    (diag_dir / "bundle-r2.json").write_text(json.dumps(bundle), encoding="utf-8")

    report = build_maintenance_report(
        root=tmp_path,
        diagnostics_dir=diag_dir,
    )

    titles = {finding["title"] for finding in report["findings"]}
    assert "Diagnostic ended with an empty assistant reply" in titles
    assert "Missing measurement cache" in titles
    assert "Fit-ready measurements coexisted with synthetic fallback" in titles

    markdown = render_markdown_report(report)
    assert "Maintenance Report" in markdown
    assert "bundle-r2" in markdown


def test_maintenance_lock_acquire_status_release(tmp_path: Path):
    from app.services.maintenance import acquire_lock, read_lock, release_lock

    acquired = acquire_lock(owner="codex", root=tmp_path)
    assert acquired["acquired"] is True
    assert read_lock(tmp_path)["owner"] == "codex"

    blocked = acquire_lock(owner="claude", root=tmp_path)
    assert blocked["acquired"] is False
    assert blocked["lock"]["owner"] == "codex"

    mismatch = release_lock(owner="claude", root=tmp_path)
    assert mismatch["released"] is False
    assert mismatch["reason"] == "owner_mismatch"

    released = release_lock(owner="codex", root=tmp_path)
    assert released["released"] is True
    assert read_lock(tmp_path) is None
