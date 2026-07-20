#!/usr/bin/env python
"""Start and record the real local Union3 reproduction loop.

The runner starts a real FastAPI backend, Vite frontend, outbound-only science
worker CLI, and the production independent-verification reconciler. Ephemeral
keys and the SQLite ledger live under the selected state directory and are
deleted unless ``--keep-state`` is requested.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.fernet import Fernet


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"
DEFAULT_BACKEND_PYTHON = Path(
    "/Users/chenkexuan/Projects/astro-platform/backend/venv/bin/python"
)
_CLIENT_ENV_ALLOWLIST = frozenset(
    {
        "ALL_PROXY",
        "CURL_CA_BUNDLE",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


def _raw_keys() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def _full_git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("The demo needs one full Git commit identity")
    return value


def _is_dirty() -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(completed.stdout.strip())


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_for_url(url: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 500:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _demo_environment(
    state: Path,
    commit: str,
    *,
    frontend_port: int,
    redis_port: int,
) -> tuple[dict[str, str], dict[str, str]]:
    worker_private, worker_public = _raw_keys()
    evidence_private, evidence_public = _raw_keys()
    common = os.environ.copy()
    common.update(
        {
            "ENV": "dev",
            "DATABASE_URL": f"sqlite+aiosqlite:///{state / 'astro-demo.db'}",
            "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
            "GIT_COMMIT": commit,
            "TOOL_VERSION": commit,
            "JWT_SECRET": secrets.token_urlsafe(48),
            "FERNET_KEY": Fernet.generate_key().decode("ascii"),
            "DELETION_TOMBSTONE_KEY": secrets.token_urlsafe(48),
            "DELETION_TOMBSTONE_KEY_ID": "demo-deletion-v1",
            "EVIDENCE_SIGNING_KEY": secrets.token_urlsafe(48),
            "EVIDENCE_SIGNING_KEY_ID": "demo-hmac-v1",
            "SIGNUP_MODE": "public",
            "SCIENCE_EXECUTION_BACKEND": "https_worker",
            "CLAIM_AUDIT_EXECUTION_MODE": "https_worker",
            "CLAIM_AUDIT_ENABLED": "true",
            "RESEARCH_WORKSPACE_ENABLED": "true",
            "ARXIV_READER_ENABLED": "true",
            "UNION3_REPRODUCTION_ENABLED": "true",
            "EVIDENCE_PACK_V2_ENABLED": "true",
            "LOCAL_SCIENCE_WORKER_ENABLED": "true",
            "SHARED_DEEPSEEK_API_KEY_ENABLED": "false",
            "SCIENTIFIC_REVIEWER_USERNAMES": "astro-reviewer",
            "WORKER_TASK_SIGNING_PRIVATE_KEY": worker_private,
            "WORKER_TASK_SIGNING_KEY_ID": "demo-worker-task-v1",
            "WORKER_TASK_SIGNING_PUBLIC_KEY": worker_public,
            "WORKER_TASK_VERIFICATION_KEYS": json.dumps(
                {"demo-worker-task-v1": worker_public}, separators=(",", ":")
            ),
            "EVIDENCE_V2_SIGNING_PRIVATE_KEY": evidence_private,
            "EVIDENCE_V2_SIGNING_KEY_ID": "demo-evidence-v2",
            "EVIDENCE_V2_SIGNING_PUBLIC_KEY": evidence_public,
            "EVIDENCE_V2_VERIFICATION_KEYS": json.dumps(
                {"demo-evidence-v2": evidence_public}, separators=(",", ":")
            ),
            "STORAGE_BACKEND": "local",
            "LOCAL_STORAGE_DIR": str(state / "objects"),
            "PERSISTENT_STORAGE_MOUNT": str(state),
            "GATE_EVENTS_JSONL_PATH": str(state / "gate-events.jsonl"),
            "CORS_ORIGINS": (
                f"http://127.0.0.1:{frontend_port},"
                f"http://localhost:{frontend_port}"
            ),
            "PRIVACY_OPERATOR_NAME": "Local demo operator",
            "PRIVACY_CONTACT": "local-demo@example.invalid",
            "PRIVACY_JURISDICTION": "Local development only",
            "RATE_LIMIT_ENABLED": "false",
            "CONNECTOR_CACHE_BACKEND": "null",
            "ASTRO_RESEARCH_FOCUS": "cosmology",
        }
    )
    api = common | {"APP_ROLE": "api"}
    control = common | {
        "APP_ROLE": "control_worker",
        # The control worker verifies the public task key. It never receives
        # the API's task-envelope private key.
        "WORKER_TASK_SIGNING_PRIVATE_KEY": "",
    }
    return api, control


class Processes:
    def __init__(self) -> None:
        self._items: list[tuple[str, subprocess.Popen[bytes], object]] = []

    def start(
        self,
        name: str,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
    ) -> None:
        log = log_path.open("wb")
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._items.append((name, process, log))

    def assert_alive(self) -> None:
        failed = [name for name, process, _ in self._items if process.poll() is not None]
        if failed:
            raise RuntimeError(f"Demo process exited early: {', '.join(failed)}")

    def stop(self) -> None:
        for _name, process, _log in reversed(self._items):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 10
        for _name, process, _log in reversed(self._items):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        for _name, _process, log in self._items:
            log.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _client_environment() -> dict[str, str]:
    """Return a Worker/browser environment stripped of control-plane secrets."""

    return {
        key: value
        for key, value in os.environ.items()
        if key in _CLIENT_ENV_ALLOWLIST
    }


def _prepare_playwright_ffmpeg(state: Path) -> Path:
    """Expose the audited system ffmpeg at Playwright's expected cache path."""

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg is None:
        raise RuntimeError("MP4 recording needs ffmpeg")
    cache = state / "playwright-browsers"
    executable_name = {
        "darwin": "ffmpeg-mac",
        "linux": "ffmpeg-linux",
        "win32": "ffmpeg-win64.exe",
    }.get(sys.platform)
    if executable_name is None:
        raise RuntimeError(f"Unsupported Playwright recording platform: {sys.platform}")
    target = cache / "ffmpeg-1011" / executable_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.symlink_to(system_ffmpeg)
    return cache


def _seed_registered_source_cache(state: Path, cache_dir: Path) -> None:
    """Seed only checksum-matching arXiv bytes into the local object cache."""

    sys.path.insert(0, str(BACKEND))
    from app.services.union3_reader import (  # noqa: PLC0415
        UNION3_ARXIV_ID,
        UNION3_PDF_SHA256,
        UNION3_SOURCE_TAR_SHA256,
    )

    sources = (
        (
            cache_dir / f"{UNION3_ARXIV_ID}.tar.gz",
            UNION3_SOURCE_TAR_SHA256,
            f"{UNION3_SOURCE_TAR_SHA256}.tar.gz",
        ),
        (
            cache_dir / f"{UNION3_ARXIV_ID}.pdf",
            UNION3_PDF_SHA256,
            f"{UNION3_PDF_SHA256}.pdf",
        ),
    )
    destination_dir = (
        state / "objects" / "source-snapshots" / "union3" / UNION3_ARXIV_ID
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    for source, expected_hash, destination_name in sources:
        if not source.is_file():
            raise RuntimeError(f"Registered source-cache file is missing: {source}")
        observed_hash = _sha256(source)
        if not secrets.compare_digest(observed_hash, expected_hash):
            raise RuntimeError(
                f"Registered source-cache checksum mismatch for {source.name}: "
                f"expected {expected_hash}, observed {observed_hash}"
            )
        destination = destination_dir / destination_name
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
        sidecar = destination.with_name(destination.name + ".sha256")
        sidecar.write_text(expected_hash + "\n", encoding="ascii")
        sidecar.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--backend-python", type=Path)
    parser.add_argument(
        "--source-cache-dir",
        type=Path,
        help=(
            "Optional directory containing checksum-pinned 2311.12098v4.tar.gz "
            "and 2311.12098v4.pdf files"
        ),
    )
    parser.add_argument("--backend-port", type=int, default=8010)
    parser.add_argument("--frontend-port", type=int, default=5180)
    parser.add_argument("--redis-port", type=int, default=6399)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--keep-state", action="store_true")
    args = parser.parse_args()

    backend_python = args.backend_python or Path(
        os.getenv("ASTRO_BACKEND_PYTHON", str(DEFAULT_BACKEND_PYTHON))
    )
    if not backend_python.is_file():
        raise RuntimeError(
            "Backend Python was not found; pass --backend-python or set "
            "ASTRO_BACKEND_PYTHON"
        )
    if not (FRONTEND / "node_modules" / "@playwright" / "test").is_dir():
        raise RuntimeError("Frontend dependencies are missing; run npm install first")
    if shutil.which("npm") is None or shutil.which("node") is None:
        raise RuntimeError("The demo needs npm and node")
    if shutil.which("pdftotext") is None:
        raise RuntimeError("The registered Reader needs pdftotext")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("MP4 conversion needs ffmpeg")
    requested_ports = (args.backend_port, args.frontend_port, args.redis_port)
    if len(set(requested_ports)) != len(requested_ports) or any(
        not _port_is_available(port) for port in requested_ports
    ):
        raise RuntimeError(
            "Backend, frontend, and nonce-Redis ports must be distinct and free: "
            f"{requested_ports}"
        )

    dirty = _is_dirty()
    if dirty and not args.allow_dirty:
        raise RuntimeError(
            "Refusing to publish a provenance video from a dirty tree; commit the "
            "implementation or pass --allow-dirty for a non-publishable trial"
        )

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary_state = args.state_dir is None
    state = (args.state_dir or Path(tempfile.mkdtemp(prefix="standard-astro-union3-demo-"))).resolve()
    state.mkdir(parents=True, exist_ok=True)
    (state / "objects").mkdir(exist_ok=True)
    (state / "worker-home").mkdir(exist_ok=True)
    if args.source_cache_dir is not None:
        _seed_registered_source_cache(state, args.source_cache_dir.resolve())
    commit = _full_git_commit()
    api_env, control_env = _demo_environment(
        state,
        commit,
        frontend_port=args.frontend_port,
        redis_port=args.redis_port,
    )
    api_base = f"http://127.0.0.1:{args.backend_port}"
    ui_base = f"http://127.0.0.1:{args.frontend_port}"
    frontend_env = os.environ.copy() | {
        "VITE_API_URL": api_base,
    }
    processes = Processes()
    try:
        processes.start(
            "nonce-redis",
            [
                str(backend_python),
                str(HERE / "nonce_redis.py"),
                "--port",
                str(args.redis_port),
            ],
            cwd=BACKEND,
            env=_client_environment(),
            log_path=state / "nonce-redis.log",
        )
        deadline = time.monotonic() + 10
        while _port_is_available(args.redis_port) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _port_is_available(args.redis_port):
            raise RuntimeError("The local nonce-Redis fixture did not start")
        processes.assert_alive()

        migration_env = api_env | {"APP_ROLE": "migration"}
        subprocess.run(
            [str(backend_python), "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND,
            env=migration_env,
            check=True,
        )
        processes.start(
            "backend",
            [
                str(backend_python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.backend_port),
            ],
            cwd=BACKEND,
            env=api_env,
            log_path=state / "backend.log",
        )
        _wait_for_url(f"{api_base}/health/ready", timeout=90)
        processes.assert_alive()

        processes.start(
            "control-worker",
            [str(backend_python), str(HERE / "control_worker.py")],
            cwd=BACKEND,
            env=control_env,
            log_path=state / "control-worker.log",
        )
        processes.start(
            "frontend",
            [
                "npm",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.frontend_port),
            ],
            cwd=FRONTEND,
            env=frontend_env,
            log_path=state / "frontend.log",
        )
        _wait_for_url(f"{ui_base}/research", timeout=60)
        processes.assert_alive()

        driver_env = _client_environment() | {
            "ENV": "dev",
            "APP_ROLE": "science_worker",
            "GIT_COMMIT": commit,
            "TOOL_VERSION": commit,
            "DEMO_BACKEND_PYTHON": str(backend_python),
            "DEMO_REPO": str(REPO),
            "DEMO_STATE_DIR": str(state),
            "DEMO_OUTPUT_DIR": str(output),
            "DEMO_GIT_DIRTY": "true" if dirty else "false",
            "DEMO_API_BASE": api_base,
            "DEMO_UI_BASE": ui_base,
            "DEMO_SOURCE_CACHE_PRELOADED": (
                "true" if args.source_cache_dir is not None else "false"
            ),
            "PLAYWRIGHT_BROWSERS_PATH": str(_prepare_playwright_ffmpeg(state)),
        }
        subprocess.run(
            ["node", str(HERE / "record_demo.mjs")],
            cwd=FRONTEND,
            env=driver_env,
            check=True,
        )
        receipt_path = output / "union3-demo-receipt.json"
        video_path = output / "standard-astro-union3-local-worker-demo.mp4"
        if not receipt_path.is_file() or not video_path.is_file():
            raise RuntimeError("The browser driver did not produce its receipt and MP4")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("scientific_verdict") != "WITHHELD":
            raise RuntimeError("The unbound host run did not remain safely WITHHELD")
        if receipt.get("publication_ready") is not False:
            raise RuntimeError("The recorded reproduction incorrectly became publication-ready")
        verification = receipt.get("independent_verification") or {}
        if (
            verification.get("passed") is not False
            or verification.get("fail_closed") is not True
            or verification.get("error_class") != "worker_task_binding_mismatch"
        ):
            raise RuntimeError("The OCI-binding gate did not produce the expected refusal")
        if receipt.get("evidence_pack_created") is not False:
            raise RuntimeError("A WITHHELD run must not create a finalized Evidence Pack")
        summary = {
            "video": str(video_path),
            "video_sha256": _sha256(video_path),
            "receipt": str(receipt_path),
            "git_commit": commit,
            "git_dirty": dirty,
            "state_dir": str(state) if args.keep_state else None,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        processes.stop()
        if temporary_state and not args.keep_state:
            shutil.rmtree(state, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"union3 demo: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
