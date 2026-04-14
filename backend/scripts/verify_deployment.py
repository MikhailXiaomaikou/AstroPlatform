"""Post-deployment verification script.

Checks health endpoints, API documentation, and basic connectivity.

Usage:
    python scripts/verify_deployment.py https://your-backend-url.com
    python scripts/verify_deployment.py  # defaults to localhost:8000
"""

import sys

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: pip install httpx")
    sys.exit(1)


def verify(base_url: str) -> bool:
    checks = [
        ("Health endpoint", f"{base_url}/health", 200),
        ("Health stats", f"{base_url}/health/stats", 200),
        ("OpenAPI schema", f"{base_url}/openapi.json", 200),
        ("Swagger docs", f"{base_url}/docs", 200),
        ("ReDoc", f"{base_url}/redoc", 200),
    ]

    passed = 0
    failed = 0

    print(f"\nVerifying deployment at: {base_url}\n")

    for name, url, expected_status in checks:
        try:
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            ok = resp.status_code == expected_status
            status = "PASS" if ok else "FAIL"
            icon = "\u2705" if ok else "\u274c"
            print(f"  {icon} {name}: {resp.status_code} (expected {expected_status})")
            if ok:
                passed += 1
            else:
                failed += 1
        except httpx.ConnectError:
            print(f"  \u274c {name}: Connection refused")
            failed += 1
        except httpx.TimeoutException:
            print(f"  \u274c {name}: Timeout (30s)")
            failed += 1
        except Exception as e:
            print(f"  \u274c {name}: {e}")
            failed += 1

    # Check health response content
    try:
        resp = httpx.get(f"{base_url}/health", timeout=10)
        data = resp.json()
        print(f"\n  Version: {data.get('version', 'unknown')}")
        print(f"  Status: {data.get('status', 'unknown')}")
    except Exception:
        pass

    # Check stats
    try:
        resp = httpx.get(f"{base_url}/health/stats", timeout=10)
        data = resp.json()
        uptime = data.get("uptime_seconds", 0)
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60
        print(f"  Uptime: {hours}h {minutes}m")
        print(f"  Total requests: {data.get('requests_total', 0)}")
        print(f"  Error rate: {data.get('error_rate', 0):.2%}")
    except Exception:
        pass

    print(f"\n  Result: {passed}/{passed + failed} checks passed\n")
    return failed == 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    url = url.rstrip("/")
    success = verify(url)
    sys.exit(0 if success else 1)
