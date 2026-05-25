"""PART AD: cached:<key> must resolve to a live cache entry before run_python
executes.

Previously the key was trusted blindly ("free-form, no static check"), so a
non-existent key let the run proceed and the code could silently fabricate
when get_cached_results(<missing>) returned None.
"""

from __future__ import annotations

import asyncio


def test_cached_key_not_found_is_rejected():
    from app.services.ai_tools import _exec_run_python

    r = asyncio.run(
        _exec_run_python(
            {
                "code": "rows = get_cached_results('ghost_key')\nprint(len(rows))",
                "data_source": "cached:ghost_key",
            }
        )
    )
    assert r["success"] is False
    assert r["error_class"] == "cached_key_not_found"


def test_cached_key_present_passes_the_guard():
    from app.services import ai_tools
    from app.services.ai_tools import _exec_run_python

    ai_tools.store_search_results("live_key_guard_test", [{"x": 1.0}, {"x": 2.0}])
    r = asyncio.run(
        _exec_run_python(
            {
                "code": "print('ok')",
                "data_source": "cached:live_key_guard_test",
            }
        )
    )
    # The guard must not fire for a live key — any downstream outcome is fine
    # as long as it is NOT the cached_key_not_found rejection.
    assert r.get("error_class") != "cached_key_not_found"


def test_user_file_csv_without_declaration_not_synthetic_rejected():
    """PART AD: reading your own CSV via pd.read_csv with no data_source must
    auto-classify as user_file (real data), NOT get rejected as a synthetic
    declaration / mismatch."""
    from app.services.ai_tools import _exec_run_python

    r = asyncio.run(
        _exec_run_python(
            {
                "code": (
                    "import pandas as pd\n"
                    "df = pd.read_csv('my_data.csv')\n"
                    "print(len(df))"
                ),
                # no data_source declared — auto-classification must pick user_file
            }
        )
    )
    assert r.get("error_class") not in (
        "incorrect_synthetic_declaration",
        "data_source_mismatch",
        "synthetic_declared_as_real",
    )
