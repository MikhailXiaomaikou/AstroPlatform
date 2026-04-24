from __future__ import annotations


def test_local_diagnostic_bundle_redacts_and_replays(tmp_path):
    from app.services.local_diagnostics import (
        build_diagnostic_bundle,
        replay_diagnostic_bundle,
        write_diagnostic_bundle,
    )

    bundle = build_diagnostic_bundle(
        prompt="fit [CII]",
        model_config={"api_key": "sk-secret", "provider": "anthropic"},
        tool_results=[{
            "tool": "search_literature",
            "input": {"Authorization": "Bearer secret"},
            "result": {"success": True, "results": []},
        }],
        final_reply="No measurement table was obtained.",
        validator_results={"ok": True},
        frontend_summary={"total": 1},
    )
    path = write_diagnostic_bundle(bundle, tmp_path)
    text = path.read_text()

    assert "sk-secret" not in text
    assert "Bearer secret" not in text
    assert "[REDACTED]" in text

    replay = replay_diagnostic_bundle(path)
    assert replay["bundle_id"] == bundle["bundle_id"]
    assert replay["numeric_ok"] is True
    assert replay["would_withhold"] is False


def test_local_diagnostic_sanitizer_truncates_large_payloads():
    from app.services.local_diagnostics import sanitize_for_diagnostic

    result = sanitize_for_diagnostic({
        "rows": list(range(60)),
        "stdout": "x" * 2100,
        "cookie": "secret-cookie",
    })

    assert result["cookie"] == "[REDACTED]"
    assert result["rows"][-1] == "[truncated 10 items]"
    assert "truncated" in result["stdout"]
