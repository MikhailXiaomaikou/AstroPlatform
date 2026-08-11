"""The zero-caller routers stay unmounted unless explicitly enabled.

2026-08-09 surface audit: these ten routers have no frontend page, no
chat-tool HTTP path, and no worker HTTP caller. Production defaults keep
them unmounted; the test process remounts them via conftest so their
implementations stay exercised.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

GATED_PREFIXES = (
    "/api/pipeline",
    "/api/scheduler",
    "/api/crossmatch",
    "/api/isochrones",
    "/api/literature/citation-graph",
    "/api/integration",
    "/api/workspace",
    "/api/provenance",
    "/api/user-tools",
    "/api/admin/inference",
)

_DUMP_ROUTES = (
    "import json; from app.main import app; "
    "print(json.dumps(sorted({getattr(r, 'path', '') for r in app.routes})))"
)


def _routes_with_env(value: str | None) -> list[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "ZERO_CALLER_ROUTERS_ENABLED"
    }
    if value is not None:
        env["ZERO_CALLER_ROUTERS_ENABLED"] = value
    out = subprocess.run(
        [sys.executable, "-c", _DUMP_ROUTES],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_default_app_does_not_mount_zero_caller_routers():
    routes = _routes_with_env(None)
    mounted = [p for p in routes if p.startswith(GATED_PREFIXES)]
    assert mounted == []


def test_flag_remounts_zero_caller_routers():
    routes = _routes_with_env("1")
    for prefix in GATED_PREFIXES:
        assert any(p.startswith(prefix) for p in routes), prefix
