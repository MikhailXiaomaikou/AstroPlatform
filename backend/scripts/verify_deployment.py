"""Post-deployment verification script.

Checks liveness, bounded traffic readiness, full deployment readiness, release
identity, API documentation, and optional admin statistics.

Usage:
    python scripts/verify_deployment.py https://your-backend-url.com
    EXPECTED_COMMIT=<full-sha> python scripts/verify_deployment.py <url>
    python scripts/verify_deployment.py  # defaults to localhost:8000
"""

import ipaddress
import os
import re
import sys
from typing import Any
from urllib.parse import urlparse

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: pip install httpx")
    sys.exit(1)


_UNKNOWN_COMMITS = {"", "unknown", "none", "null"}
_FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)


def _is_loopback_url(base_url: str) -> bool:
    """Return whether ``base_url`` resolves syntactically to a loopback host."""
    hostname = (urlparse(base_url).hostname or "").strip().lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    """Return the operator-safe payload from either a 2xx or FastAPI error."""
    data = response.json()
    if not isinstance(data, dict):
        return {}
    detail = data.get("detail")
    return detail if isinstance(detail, dict) else data


def _commit(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "unknown"
    version = payload.get("version")
    if not isinstance(version, dict):
        return "unknown"
    return str(version.get("commit") or "unknown").strip()


def verify(base_url: str, *, expected_commit: str | None = None) -> bool:
    base_url = base_url.rstrip("/")
    if expected_commit is None:
        expected_commit = os.getenv("EXPECTED_COMMIT", "")
    expected_commit = str(expected_commit or "").strip()
    if not _is_loopback_url(base_url):
        if _FULL_GIT_SHA.fullmatch(expected_commit) is None:
            print(
                "\n\u274c Remote deployment verification requires EXPECTED_COMMIT "
                "to be the full 40-character Git SHA.\n"
            )
            return False
        expected_commit = expected_commit.lower()

    checks = [
        ("Liveness", f"{base_url}/health", 200),
        ("OpenAPI schema", f"{base_url}/openapi.json", 200),
        ("Swagger docs", f"{base_url}/docs", 200),
        ("ReDoc", f"{base_url}/redoc", 200),
    ]

    passed = 0
    failed = 0

    def record(name: str, ok: bool, detail: str) -> None:
        nonlocal passed, failed
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}: {detail}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\nVerifying deployment at: {base_url}\n")

    health_payload: dict[str, Any] | None = None
    for name, url, expected_status in checks:
        try:
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            ok = resp.status_code == expected_status
            record(name, ok, f"{resp.status_code} (expected {expected_status})")
            if name == "Liveness":
                try:
                    health_payload = _response_payload(resp)
                except Exception:
                    health_payload = None
        except httpx.ConnectError:
            record(name, False, "connection refused")
        except httpx.TimeoutException:
            record(name, False, "timeout (30s)")
        except Exception as e:
            record(name, False, str(e))

    if health_payload:
        print(f"\n  Version: {health_payload.get('version', 'unknown')}")
        print(f"  Status: {health_payload.get('status', 'unknown')}")

    # Fetch each readiness surface exactly once. A 200 status alone is
    # insufficient: proxies and future handlers must not turn a fail-closed body
    # into a false deployment success.
    readiness_payloads: dict[str, dict[str, Any] | None] = {
        "ready": None,
        "deep": None,
    }
    readiness_checks = (
        ("ready", "Traffic readiness", "/health/ready", "ready"),
        ("deep", "Full readiness", "/health/deep", True),
    )
    for key, name, path, expected_body in readiness_checks:
        try:
            response = httpx.get(
                f"{base_url}{path}", timeout=15, follow_redirects=True
            )
            payload = _response_payload(response)
            readiness_payloads[key] = payload
            body_ok = (
                payload.get("status") == expected_body
                if key == "ready"
                else payload.get("ok") is expected_body
            )
            ok = response.status_code == 200 and body_ok
            record(name, ok, f"{response.status_code}; components={payload.get('components', {})}")
        except httpx.ConnectError:
            record(name, False, "connection refused")
        except httpx.TimeoutException:
            record(name, False, "timeout (15s)")
        except Exception as exc:
            record(name, False, f"invalid response: {exc}")

    ready_commit = _commit(readiness_payloads["ready"])
    deep_commit = _commit(readiness_payloads["deep"])
    commits_known = (
        ready_commit.lower() not in _UNKNOWN_COMMITS
        and deep_commit.lower() not in _UNKNOWN_COMMITS
    )
    same_release = commits_known and ready_commit.lower() == deep_commit.lower()
    expected_release = not expected_commit or ready_commit.lower() == expected_commit
    identity_ok = same_release and expected_release
    identity_detail = f"ready={ready_commit}, deep={deep_commit}"
    if expected_commit:
        identity_detail += f", expected={expected_commit}"
    record("Release identity", identity_ok, identity_detail)

    # Admin stats are protected; check them only when the operator explicitly
    # supplies the secret in the environment.
    admin_secret = os.getenv("ADMIN_SECRET", "").strip()
    if admin_secret:
        try:
            resp = httpx.get(
                f"{base_url}/health/stats",
                headers={"X-Admin-Secret": admin_secret},
                timeout=10,
            )
            if resp.status_code != 200:
                record("Admin health stats", False, str(resp.status_code))
            else:
                data = resp.json()
                uptime = data.get("uptime_seconds", 0)
                hours = uptime // 3600
                minutes = (uptime % 3600) // 60
                print(f"  Uptime: {hours}h {minutes}m")
                print(f"  Total requests: {data.get('requests_total', 0)}")
                print(f"  Error rate: {data.get('error_rate', 0):.2%}")
                record("Admin health stats", True, "200")
        except Exception as exc:
            record("Admin health stats", False, str(exc))

    print(f"\n  Result: {passed}/{passed + failed} checks passed\n")
    return failed == 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    url = url.rstrip("/")
    success = verify(url)
    sys.exit(0 if success else 1)
