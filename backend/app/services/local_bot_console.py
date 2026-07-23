"""Local-only bridge for the Bot Console UI.

The browser never talks to Telegram, ``launchctl``, or the Codex CLI directly.
This module exposes the two server-side capabilities needed by the local UI:

* a strict, no-tools, no-fallback completion from one pinned local model;
* read-only research status/report access plus one fixed launchd trigger.

All paths and process arguments are chosen by the server.  Nothing supplied by
the HTTP client is interpolated into a filesystem path or command line.

Every operator-specific value (pipeline root, launchd job labels, schedule
text, pinned model) is read from the environment with no personal default, so
a self-hoster can point this console at their own local automation without
editing source.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import logging
import os
import platform
import re
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.ai.inference_router import LocalBackend
from app.ai.model_profiles import resolve_model_profile


logger = logging.getLogger(__name__)

FINAL_RESEARCH_STATUSES = {"completed", "completed_with_warnings", "no_new_data"}
RETRIABLE_NOTIFICATION_STATUSES = {"pending", "failed"}
KNOWN_NOTIFICATION_STATUSES = {"pending", "failed", "sent", "skipped", "unknown"}
_REPORT_NAME_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}\.md$")
_MAX_STATE_BYTES = 1_048_576
_MAX_REPORT_BYTES = 1_048_576
_MAX_REPORT_CHARS = 200_000
_SOL_CONCURRENCY = asyncio.Semaphore(1)

_BOT_SYSTEM_PROMPT = """You are the operator's pinned local model, reachable through this
astronomy research automation console. Answer directly and concisely; state
clearly when you are uncertain. This conversation has no tools available. Do
not claim to have called a tool, read a local file, or accessed information
that was not provided."""

_STATUS_LABELS = {
    "pending": "Waiting to run",
    "running": "Running",
    "completed": "Completed",
    "completed_with_warnings": "Completed with review items",
    "no_new_data": "No new data this week",
    "failed": "Failed; recovery pending",
    "interrupted": "Interrupted; recovery pending",
    "unavailable": "Unavailable",
    "unknown": "Unknown status",
}


def sol_model() -> str:
    """The pinned local model id, configurable per self-hosted deployment."""

    return os.getenv("BOT_CONSOLE_MODEL_ID", "").strip() or "local-console-model"


def _sol_profile() -> str:
    profile = os.getenv("BOT_CONSOLE_MODEL_PROFILE", "").strip() or "local:openai-cli"
    if profile != "local:openai-cli":
        raise SolUnavailable("the Bot Console only supports the local OpenAI CLI")
    return profile


def _research_launchd_label() -> str:
    return os.getenv("RESEARCH_LAUNCHD_LABEL", "").strip()


def _bot_launchd_label() -> str:
    return os.getenv("BOT_LAUNCHD_LABEL", "").strip()


def _schedule_primary() -> str | None:
    return os.getenv("RESEARCH_SCHEDULE_PRIMARY", "").strip() or None


def _schedule_recovery() -> str | None:
    return os.getenv("RESEARCH_SCHEDULE_RECOVERY", "").strip() or None


def _launchd_supported() -> bool:
    return platform.system() == "Darwin"


class LocalBotConsoleError(RuntimeError):
    """Base error whose private details must not be returned by the API."""


class SolUnavailable(LocalBotConsoleError):
    """The pinned local SOL backend is unavailable or failed."""


class ResearchUnavailable(LocalBotConsoleError):
    """The local research files or launchd job are unavailable."""


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def local_runtime_enabled() -> bool:
    """Return whether local subscription-backed execution is allowed here."""

    return os.getenv("ENV", "dev").strip().lower() not in {
        "production",
        "prod",
    }


def is_loopback_peer(host: str | None) -> bool:
    """Check the transport peer without trusting forwarded HTTP headers."""

    if not host:
        return False
    value = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def allowed_local_origins() -> set[str]:
    """Exact browser origins allowed to operate the local console."""

    raw = os.getenv("LOCAL_CONTROL_ORIGINS", "").strip()
    if raw:
        return {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}
    return {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }


async def chat_with_sol(messages: list[dict[str, str]]) -> str:
    """Return a strict GPT-5.6 SOL completion with no tools or fallback.

    Calling ``LocalBackend.complete`` directly intentionally bypasses
    ``InferenceRouter.route`` and its provider fallback chain.  The explicit
    OPENAI_CLI check also prevents ``LocalBackend`` from falling through to an
    OpenAI-compatible local server when the subscription CLI is disabled.
    """

    if not local_runtime_enabled() or not _env_truthy("OPENAI_CLI_ENABLED"):
        raise SolUnavailable("the local OpenAI CLI bridge is disabled")

    profile = replace(
        resolve_model_profile("local", _sol_profile()),
        resolved_model_id=sol_model(),
    )
    backend = LocalBackend()
    try:
        async with _SOL_CONCURRENCY:
            result = await backend.complete(
                messages,
                system=_BOT_SYSTEM_PROMPT,
                tools=None,
                max_tokens=4096,
                request_timeout=180.0,
                model_profile=profile,
            )
    except Exception as exc:
        logger.warning("Pinned local SOL request failed: %s", exc)
        raise SolUnavailable("pinned local SOL request failed") from exc

    content = str(result.get("content") or "").strip()
    if not content or result.get("tool_calls"):
        logger.warning("Pinned local SOL returned an invalid response shape")
        raise SolUnavailable("pinned local SOL returned no plain-text answer")
    return content


def current_week_id(day: dt.date | None = None) -> str:
    iso = (day or dt.date.today()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _cosmo_root() -> Path:
    raw = os.getenv("COSMO_SECOND_ORDER_ROOT", "").strip()
    if not raw:
        raise ResearchUnavailable("research root is not configured")
    root = Path(raw).expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        logger.info("Research root is unavailable: %s", exc)
        raise ResearchUnavailable("research root is unavailable") from exc
    if not root.is_dir():
        raise ResearchUnavailable("research root is not a directory")
    return root


def _read_small_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_STATE_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_state(root: Path, week_id: str) -> dict[str, Any] | None:
    weeks_root = root / ".pipeline_state" / "weeks"
    path = weeks_root / f"{week_id}.json"
    try:
        resolved_weeks = weeks_root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_weeks)
    except (OSError, ValueError):
        return None
    return _read_small_json(resolved_path)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, parsed)


def _research_status_value(state: dict[str, Any] | None) -> str:
    raw = str(state.get("status") or "pending").strip().lower() if state else "pending"
    return raw if raw in _STATUS_LABELS else "unknown"


def _notification_status_value(state: dict[str, Any] | None) -> str | None:
    notification = state.get("notification") if isinstance(state, dict) else None
    if not isinstance(notification, dict):
        return None
    raw = str(notification.get("status") or "").strip().lower()
    if not raw:
        return None
    return raw if raw in KNOWN_NOTIFICATION_STATUSES else "unknown"


def _report_candidate(root: Path, raw: object) -> Path | None:
    """Resolve one report path while rejecting traversal and every symlink."""

    if not isinstance(raw, (str, os.PathLike)):
        return None
    reports_root = root / "reports"
    try:
        if reports_root.is_symlink():
            return None
        safe_root = reports_root.resolve(strict=True)
    except OSError:
        return None

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    # Reports are deliberately flat and date-named.  This rejects traversal,
    # nested directories, temporary files, and surprising state-file values.
    if not _REPORT_NAME_RE.fullmatch(candidate.name):
        return None
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(safe_root)
        if len(relative.parts) != 1 or resolved.is_symlink():
            return None
        file_stat = resolved.stat()
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > _MAX_REPORT_BYTES:
        return None
    return resolved


def _latest_safe_report(root: Path, state: dict[str, Any] | None) -> Path | None:
    if state is not None:
        candidate = _report_candidate(root, state.get("report"))
        if candidate is not None:
            return candidate

    reports_root = root / "reports"
    try:
        entries = list(reports_root.iterdir())
    except OSError:
        return None
    safe: list[Path] = []
    for entry in entries:
        candidate = _report_candidate(root, entry)
        if candidate is not None:
            safe.append(candidate)
    if not safe:
        return None
    try:
        return max(safe, key=lambda item: item.stat().st_mtime)
    except OSError:
        return None


def _read_report_no_follow(path: Path) -> tuple[bytes, os.stat_result] | None:
    """Open a checked report without following a last-moment symlink swap."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > _MAX_REPORT_BYTES:
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_REPORT_BYTES + 1)
        if len(raw) > _MAX_REPORT_BYTES:
            return None
        return raw, file_stat
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _can_trigger(state: dict[str, Any] | None) -> bool:
    if state is None:
        return True
    status_value = _research_status_value(state)
    if status_value == "running":
        return False
    notification_status = _notification_status_value(state) or ""
    if status_value in FINAL_RESEARCH_STATUSES:
        return notification_status in RETRIABLE_NOTIFICATION_STATUSES
    return True


def _run_bot_status_check() -> bool:
    if not _launchd_supported():
        return False
    label = _bot_launchd_label()
    if not label:
        return False
    domain = f"gui/{os.getuid()}/{label}"
    try:
        result = subprocess.run(
            ["/bin/launchctl", "print", domain],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            shell=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "state = running" in (result.stdout or "")


async def research_status() -> dict[str, Any]:
    """Return a small structured status with no local filesystem paths."""

    root = _cosmo_root()
    week_id = current_week_id()
    state = _load_state(root, week_id)
    status_value = _research_status_value(state)
    budget = state.get("budget") if isinstance(state, dict) else None
    quality = state.get("execution_quality") if isinstance(state, dict) else None
    report = _latest_safe_report(root, state)
    bot_online = await asyncio.to_thread(_run_bot_status_check)

    return {
        "week_id": week_id,
        "status": status_value,
        "status_label": _STATUS_LABELS.get(status_value, status_value),
        "bot_online": bot_online,
        "model": sol_model(),
        "schedule_primary": _schedule_primary(),
        "schedule_recovery": _schedule_recovery(),
        "llm_calls_used": _safe_int(
            budget.get("llm_calls_used") if isinstance(budget, dict) else 0
        ),
        "llm_calls_limit": _safe_int(
            budget.get("llm_calls_limit") if isinstance(budget, dict) else 10,
            default=10,
        ),
        "executed_success": _safe_int(
            quality.get("executed_success") if isinstance(quality, dict) else 0
        ),
        "needs_review": _safe_int(
            quality.get("needs_review") if isinstance(quality, dict) else 0
        ),
        "report_available": report is not None,
        "report_name": report.name if report is not None else None,
        "notification_status": _notification_status_value(state),
        "can_trigger": _can_trigger(state),
    }


def _week_id_for_report(report: Path, file_stat: os.stat_result) -> str:
    """Label the report with the week it belongs to, not the current week.

    The latest safe report can be last week's file when the current week has
    not run yet; labelling it with the current week misattributes it. Report
    names are dated (``2026-07-11.md``), with the file mtime as fallback.
    """

    try:
        report_date = dt.date.fromisoformat(report.stem)
    except ValueError:
        report_date = dt.datetime.fromtimestamp(
            file_stat.st_mtime, tz=dt.timezone.utc
        ).date()
    return current_week_id(report_date)


def research_report() -> dict[str, Any] | None:
    """Return the latest safe report, capped for a browser response."""

    root = _cosmo_root()
    week_id = current_week_id()
    state = _load_state(root, week_id)
    report = _latest_safe_report(root, state)
    if report is None:
        return None
    opened = _read_report_no_follow(report)
    if opened is None:
        return None
    raw, file_stat = opened
    week_id = _week_id_for_report(report, file_stat)
    text = raw.decode("utf-8", errors="replace")
    truncated = len(text) > _MAX_REPORT_CHARS
    if truncated:
        text = text[:_MAX_REPORT_CHARS].rstrip() + "\n\n[Report truncated: too long to display in full]"
    updated_at = dt.datetime.fromtimestamp(
        file_stat.st_mtime,
        tz=dt.timezone.utc,
    ).isoformat()
    return {
        "week_id": week_id,
        "report_name": report.name,
        "markdown": text,
        "truncated": truncated,
        "updated_at": updated_at,
    }


def _run_launchctl(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run one fixed launchctl argv without a shell or captured diagnostics."""

    return subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
        shell=False,
    )


async def trigger_research() -> dict[str, Any]:
    """Idempotently submit the fixed weekly launchd job."""

    root = _cosmo_root()
    week_id = current_week_id()
    state = _load_state(root, week_id)
    status_value = _research_status_value(state)

    if status_value == "running":
        return {
            "week_id": week_id,
            "submitted": False,
            "status": status_value,
            "message": "A research run is already in progress; it will not be started again.",
        }
    if not _can_trigger(state):
        return {
            "week_id": week_id,
            "submitted": False,
            "status": status_value,
            "message": "This week's research is already complete; it will not be recomputed.",
        }

    if not _launchd_supported():
        raise ResearchUnavailable("launchd job control is only supported on macOS")
    label = _research_launchd_label()
    if not label:
        raise ResearchUnavailable("the weekly launchd job label is not configured")

    domain = f"gui/{os.getuid()}/{label}"
    print_cmd = ["/bin/launchctl", "print", domain]
    kickstart_cmd = ["/bin/launchctl", "kickstart", domain]
    try:
        checked = await asyncio.to_thread(_run_launchctl, print_cmd)
        if checked.returncode != 0:
            raise ResearchUnavailable("weekly launchd job is not loaded")
        started = await asyncio.to_thread(_run_launchctl, kickstart_cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Research launchd trigger failed: %s", exc)
        raise ResearchUnavailable("weekly launchd trigger failed") from exc
    if started.returncode != 0:
        raise ResearchUnavailable("weekly launchd trigger returned a failure")

    return {
        "week_id": week_id,
        "submitted": True,
        "status": status_value,
        "message": "Research run submitted. It runs in the background and the linked bot sends a notification when it finishes.",
    }
