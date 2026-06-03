"""G3.4 + 2026-05-20: Circuit-breaker coverage for hard-reject error_class.

A blind-test case showed: after a data-fetch tool received a local
range_too_large rejection, the LLM retried 12 times without triggering the
circuit breaker, wasting 12 LLM calls. Root cause has two layers:

1. the data-fetch tool was not in `_DATA_FETCH_TOOLS`, so the entire
   hard_failure counting logic was skipped by the outer if.
2. The soft_failure check was missing the `err_class in _HARD_REJECT_ERROR_CLASSES` short-circuit;
   future error messages containing keywords like "too large" would be wrongly classified as soft.

This file uses source-code assertions to guard both fixes against future deletion.
"""
from __future__ import annotations

import inspect


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
