"""G3.4 + 2026-05-20: Circuit-breaker coverage for hard-reject error_class.

Solar-system M0 blind test C2 case (Phaethon 100-year daily ephemeris) showed:
after fetch_horizons_ephemeris received a local range_too_large rejection, the LLM
retried 12 times without triggering the circuit breaker, wasting 12 LLM calls.
Root cause has two layers:

1. fetch_horizons_ephemeris (and 5 other solar_system data-fetch tools) were not in
   `_DATA_FETCH_TOOLS`, so the entire hard_failure counting logic was skipped by the outer if.
2. The soft_failure check was missing the `err_class in _HARD_REJECT_ERROR_CLASSES` short-circuit;
   future error messages containing keywords like "too large" would be wrongly classified as soft.

This file uses source-code assertions to guard both fixes against future deletion.
"""
from __future__ import annotations

import inspect


def test_solar_system_data_tools_in_data_fetch_set():
    """All 6 solar_system data-fetch tools must be in _DATA_FETCH_TOOLS, otherwise
    the G3.4 circuit breaker has no effect on them."""
    from app.api import chat as chat_mod

    src = inspect.getsource(chat_mod._run_agent_loop)
    for tool in [
        "query_mpc_orbit",
        "fetch_horizons_ephemeris",
        "query_sbdb_orbit",
        "query_sbdb_close_approaches",
        "query_sentry_risk",
        "query_damit_shape_model",
    ]:
        assert f'"{tool}"' in src, (
            f"{tool} 不在 _DATA_FETCH_TOOLS — G3.4 熔断对它无效, "
            f"参考 chat.py L4166 的 `if tool_name in _DATA_FETCH_TOOLS`"
        )


def test_hard_reject_error_classes_defined():
    """_HARD_REJECT_ERROR_CLASSES frozenset must exist and contain the 3 core classes.

    These classes represent local hard rejections from a tool; retrying with the same
    parameters will always be rejected again, so they should follow the hard_failure
    counting path rather than soft.
    """
    from app.api import chat as chat_mod

    src = inspect.getsource(chat_mod._run_agent_loop)
    assert "_HARD_REJECT_ERROR_CLASSES" in src, (
        "_HARD_REJECT_ERROR_CLASSES frozenset 丢失"
    )
    for err_class in ["range_too_large", "missing_argument", "invalid_argument"]:
        assert f'"{err_class}"' in src, (
            f"{err_class} 必须在 _HARD_REJECT_ERROR_CLASSES 里"
        )


def test_soft_failure_short_circuits_on_hard_reject_class():
    """soft_failure check must begin with `err_class not in _HARD_REJECT_ERROR_CLASSES`
    short-circuit; otherwise error messages containing keywords like "too large" would
    still be incorrectly classified as soft."""
    from app.api import chat as chat_mod

    src = inspect.getsource(chat_mod._run_agent_loop)
    # soft_failure = ( err_class not in _HARD_REJECT_ERROR_CLASSES ... )
    assert "err_class not in _HARD_REJECT_ERROR_CLASSES" in src, (
        "soft_failure 判定缺 hard-reject short-circuit, "
        "range_too_large 这种工具本地拒绝会再次被误归 soft"
    )
