"""G3.4 + 2026-05-20: 工具熔断对硬拒绝 error_class 的覆盖.

Solar-system M0 盲测 C2 case (Phaethon 100 年 daily ephemeris) 显示:
fetch_horizons_ephemeris 收到本地 range_too_large 拒绝后, LLM 重试 12 次都没
熔断, 浪费 12 次 LLM 调用. 根因两层:

1. fetch_horizons_ephemeris (以及 5 个其他 solar_system 数据查询工具) 不在
   `_DATA_FETCH_TOOLS` 集合里, 整段 hard_failure 计数逻辑被外层 if 跳过.
2. soft_failure 判定缺 `err_class in _HARD_REJECT_ERROR_CLASSES` 短路, 未来
   error msg 含 "too large" 等关键词会被错误归入 soft.

本文件用源码断言保护这两处修复, 防止以后被误删.
"""
from __future__ import annotations

import inspect


def test_solar_system_data_tools_in_data_fetch_set():
    """6 个 solar_system 数据查询工具必须在 _DATA_FETCH_TOOLS, 否则 G3.4
    熔断对它们完全无效."""
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
    """_HARD_REJECT_ERROR_CLASSES frozenset 必须存在并含核心 3 个 class.

    这些 class 表示"工具本地硬拒绝", LLM 用同参数重试只会再次被拒, 应
    走 hard_failure 计数路径而不是 soft.
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
    """soft_failure 判定必须以 `err_class not in _HARD_REJECT_ERROR_CLASSES`
    短路开头, 否则 error message 含 "too large" 等关键词时仍会被错误归 soft."""
    from app.api import chat as chat_mod

    src = inspect.getsource(chat_mod._run_agent_loop)
    # soft_failure = ( err_class not in _HARD_REJECT_ERROR_CLASSES ... )
    assert "err_class not in _HARD_REJECT_ERROR_CLASSES" in src, (
        "soft_failure 判定缺 hard-reject short-circuit, "
        "range_too_large 这种工具本地拒绝会再次被误归 soft"
    )
