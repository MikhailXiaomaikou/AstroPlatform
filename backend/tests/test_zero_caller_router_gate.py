"""The zero-caller routers stay unmounted unless explicitly enabled.

2026-08-09 surface audit: these ten routers have no frontend page, no
chat-tool HTTP path, and no worker HTTP caller. Production defaults keep
them unmounted; the test process remounts them via conftest so their
implementations stay exercised.

The probe sets the flag inside the subprocess itself (not via inherited
environment) so the test is independent of how the CI runner propagates
env vars, and it echoes the parsed settings value so a failure names the
broken stage (env parsing vs mounting).
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


def _probe(flag_value: str | None) -> dict:
    if flag_value is None:
        set_line = "os.environ.pop('ZERO_CALLER_ROUTERS_ENABLED', None)\n"
    else:
        set_line = (
            f"os.environ['ZERO_CALLER_ROUTERS_ENABLED'] = {flag_value!r}\n"
        )
    # Enumerate via app.openapi(): FastAPI >= 0.139 stores included routers
    # as lazy _IncludedRouter wrappers without a .path, so walking
    # app.routes undercounts on newer versions while the OpenAPI paths map
    # is version-agnostic.
    prelude = (
        "import os, json\n"
        + set_line
        + "from app.config import settings\n"
        "from app.main import app\n"
        "print(json.dumps({'flag': bool(settings.zero_caller_routers_enabled),"
        " 'routes': sorted(app.openapi()['paths'].keys())}))\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "ZERO_CALLER_ROUTERS_ENABLED"
    }
    result = subprocess.run(
        [sys.executable, "-c", prelude],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_default_app_does_not_mount_zero_caller_routers():
    probe = _probe(None)
    assert probe["flag"] is False
    mounted = [p for p in probe["routes"] if p.startswith(GATED_PREFIXES)]
    assert mounted == []


def test_flag_remounts_zero_caller_routers():
    probe = _probe("1")
    assert probe["flag"] is True, "flag did not parse to True in subprocess"
    for prefix in GATED_PREFIXES:
        assert any(
            p.startswith(prefix) for p in probe["routes"]
        ), f"{prefix} missing although flag parsed True"
