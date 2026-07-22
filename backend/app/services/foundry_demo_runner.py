"""Fail-closed runner for non-formal Workflow Foundry demonstrations.

Candidate bundles are data, never executable instructions.  Every permitted
``entrypoint_id`` is bound in this module, and every result is normalized to a
non-formal contract that cannot support a Claim Audit or Evidence Pack.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.foundry_evidence_policy import (
    NON_FORMAL_EVIDENCE_CLASS,
    contains_formal_claim_escape,
    contains_formal_claim_escape_text,
)

try:  # ``resource`` is unavailable on native Windows Python.
    import resource as _resource
except ImportError:  # pragma: no cover - exercised by Windows CI/clients
    _resource = None


CANDIDATE_SCHEMA_VERSION = 1
DEMO_STATUSES = frozenset({"PASSED", "PARTIAL", "FAILED"})
_CANDIDATE_ID = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STREAM_CAPTURE_LIMIT_BYTES = 1024 * 1024
_CONTROL_RESULT_LIMIT_BYTES = 8 * 1024 * 1024
_CANDIDATE_EXECUTION_TIMEOUT_SECONDS = 300.0
_CHILD_EXIT_GRACE_SECONDS = 1.0
_STREAM_DRAIN_GRACE_SECONDS = 2.0
_CANDIDATE_CHILD_MODULE = "app.services.foundry_demo_runner"
_CANDIDATE_CHILD_ACTIVE = False


class FoundryDemoContractError(ValueError):
    """A candidate or result attempted to leave the non-formal lane."""


class _BoundedFdCapture:
    """Drain one OS pipe while retaining only a deterministic byte prefix."""

    def __init__(self, limit_bytes: int = _STREAM_CAPTURE_LIMIT_BYTES) -> None:
        super().__init__()
        self._limit_bytes = limit_bytes
        self._captured = bytearray()
        self._stop = threading.Event()
        self.observed_bytes = 0
        self.truncated = False
        self.eof_seen = False
        self.error: str | None = None
        self.forced_stop = False

    def _append(self, value: bytes) -> None:
        self.observed_bytes += len(value)
        remaining = self._limit_bytes - len(self._captured)
        if remaining > 0:
            self._captured.extend(value[:remaining])
        if len(value) > remaining:
            self.truncated = True

    def consume(self, descriptor: int) -> None:
        """Drain until EOF, or stop promptly if a descendant holds the pipe."""

        try:
            os.set_blocking(descriptor, False)
            while not self._stop.is_set():
                try:
                    chunk = os.read(descriptor, 64 * 1024)
                except BlockingIOError:
                    self._stop.wait(0.01)
                    continue
                except OSError as exc:
                    self.error = exc.__class__.__name__
                    break
                if not chunk:
                    self.eof_seen = True
                    break
                self._append(chunk)
        except OSError as exc:
            self.error = exc.__class__.__name__
        finally:
            try:
                os.close(descriptor)
            except OSError as exc:
                if self.error is None:
                    self.error = exc.__class__.__name__

    def stop(self) -> None:
        self.forced_stop = True
        self._stop.set()

    @property
    def capture_complete(self) -> bool:
        return self.eof_seen and self.error is None and not self.forced_stop

    def get_bytes(self) -> bytes:
        return bytes(self._captured)


def _failure_outcome(failure_class: str) -> dict[str, Any]:
    return {
        "status": "FAILED",
        "failure_class": failure_class,
        "result": {},
        "validation_summary": {"isolated_execution_complete": False},
    }


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("candidate control pipe stopped accepting bytes")
        remaining = remaining[written:]


def _terminate_candidate_group(pid: int) -> bool:
    """Best-effort process-group cleanup without leaking host exceptions."""

    try:
        os.killpg(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    except (OSError, PermissionError):
        # macOS can report EPERM for a group whose leader is already a zombie.
        # Fall back to the direct child; incomplete pipes still make the final
        # report fail closed, while the outer one-Demo Docker container remains
        # the authoritative PID namespace/cgroup cleanup boundary.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except (OSError, PermissionError):
            return False
        return False


def _wait_child(
    pid: int,
    *,
    timeout: float | None,
) -> tuple[int | None, Any]:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        try:
            if hasattr(os, "wait4"):
                waited_pid, status, usage = os.wait4(pid, os.WNOHANG)
            else:  # pragma: no cover - supported POSIX runners expose wait4
                waited_pid, status = os.waitpid(pid, os.WNOHANG)
                usage = None
        except InterruptedError:
            continue
        except ChildProcessError:
            return None, None
        if waited_pid == pid:
            return status, usage
        if deadline is not None and time.monotonic() >= deadline:
            return None, None
        time.sleep(0.005)


def _wait_child_exit_without_reaping(pid: int, timeout: float) -> bool:
    """Wait for a child to exit while keeping its PID/PGID reserved."""

    if not all(
        hasattr(os, name)
        for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    ):
        return False
    deadline = time.monotonic() + timeout
    flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
    while time.monotonic() < deadline:
        try:
            if os.waitid(os.P_PID, pid, flags) is not None:
                return True
        except InterruptedError:
            continue
        except ChildProcessError:
            return False
        time.sleep(0.005)
    return False


def _drain_capture_threads(
    captures: tuple[_BoundedFdCapture, ...],
    threads: tuple[threading.Thread, ...],
) -> None:
    deadline = time.monotonic() + _STREAM_DRAIN_GRACE_SECONDS
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    for capture, thread in zip(captures, threads):
        if thread.is_alive():
            capture.stop()
    for capture, thread in zip(captures, threads):
        thread.join(timeout=0.1)
        if thread.is_alive():
            capture.error = "collector_thread_did_not_stop"


def _decode_candidate_control(
    capture: _BoundedFdCapture,
) -> tuple[dict[str, Any] | None, str | None]:
    payload = capture.get_bytes()
    if capture.truncated:
        return None, "candidate_control_result_too_large"
    if len(payload) < 8:
        return None, "candidate_control_result_incomplete"
    expected_size = int.from_bytes(payload[:8], "big")
    if expected_size > _CONTROL_RESULT_LIMIT_BYTES:
        return None, "candidate_control_result_too_large"
    if len(payload) < expected_size + 8:
        return None, "candidate_control_result_incomplete"
    if len(payload) != expected_size + 8:
        return None, "candidate_control_result_trailing_data"
    encoded = payload[8:]
    try:
        decoded = json.loads(encoded)
        canonical = _canonical_json(decoded)
    except Exception:
        # Python's JSON decoder can also raise ValueError for the configured
        # integer-digit limit and RecursionError for adversarial nesting.
        return None, "candidate_control_result_invalid"
    if canonical != encoded:
        return None, "candidate_control_result_not_canonical"
    if not isinstance(decoded, dict):
        return None, "candidate_control_result_not_object"
    return decoded, None


def _execute_candidate_isolated(
    *,
    bundle: dict[str, Any],
    cache_root: str | Path | None,
) -> tuple[dict[str, Any], _BoundedFdCapture, _BoundedFdCapture, Any]:
    """Run a candidate in a fresh interpreter and capture all visible output.

    The production CLI is PID 1 in a one-Demo Docker container.  This process
    group provides prompt local cleanup; destroying that container/PID
    namespace is the final boundary for descendants that deliberately detach.
    """

    if _CANDIDATE_CHILD_ACTIVE:
        raise FoundryDemoContractError("candidate_nested_demo_execution_forbidden")
    if os.name != "posix" or not hasattr(os, "killpg"):
        raise FoundryDemoContractError("candidate_process_isolation_unavailable")
    request = _canonical_json(
        {
            "bundle": bundle,
            "cache_root": (
                str(Path(cache_root).resolve()) if cache_root is not None else None
            ),
        }
    )
    if len(request) > _CONTROL_RESULT_LIMIT_BYTES:
        raise FoundryDemoContractError("candidate_request_too_large")

    stdout_capture = _BoundedFdCapture()
    stderr_capture = _BoundedFdCapture()
    control_capture = _BoundedFdCapture(_CONTROL_RESULT_LIMIT_BYTES + 8)
    captures = (stdout_capture, stderr_capture, control_capture)
    threads: list[threading.Thread] = []
    open_descriptors: set[int] = set()
    process: subprocess.Popen[bytes] | None = None
    child_status: int | None = None
    child_usage: Any = None
    failure_class: str | None = None
    group_cleanup_ok = True

    try:
        with tempfile.TemporaryDirectory(prefix="standard-astro-foundry-") as temp:
            request_path = Path(temp) / "request.json"
            request_path.write_bytes(request)
            request_path.chmod(0o400)
            control_read, control_write = os.pipe()
            open_descriptors.update((control_read, control_write))
            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    _CANDIDATE_CHILD_MODULE,
                    "--candidate-child",
                    "--request",
                    str(request_path),
                    "--control-fd",
                    str(control_write),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                pass_fds=(control_write,),
                start_new_session=True,
            )
            os.close(control_write)
            open_descriptors.discard(control_write)
            if process.stdout is None or process.stderr is None:  # pragma: no cover
                failure_class = "candidate_stream_pipe_unavailable"
                raise RuntimeError(failure_class)
            stdout_read = os.dup(process.stdout.fileno())
            open_descriptors.add(stdout_read)
            stderr_read = os.dup(process.stderr.fileno())
            open_descriptors.add(stderr_read)
            process.stdout.close()
            process.stderr.close()
            for capture, descriptor in zip(
                captures,
                (stdout_read, stderr_read, control_read),
                strict=True,
            ):
                thread = threading.Thread(
                    target=capture.consume,
                    args=(descriptor,),
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
                # The collector now owns and closes this descriptor.
                open_descriptors.discard(descriptor)

            deadline = time.monotonic() + _CANDIDATE_EXECUTION_TIMEOUT_SECONDS
            frame_complete = False
            while failure_class is None and not frame_complete:
                if stdout_capture.truncated or stderr_capture.truncated:
                    failure_class = "candidate_stream_limit_exceeded"
                    break
                control = control_capture.get_bytes()
                if len(control) >= 8:
                    expected_size = int.from_bytes(control[:8], "big")
                    if expected_size > _CONTROL_RESULT_LIMIT_BYTES:
                        failure_class = "candidate_control_result_too_large"
                        break
                    frame_complete = len(control) >= expected_size + 8
                if control_capture.eof_seen and not frame_complete:
                    failure_class = "candidate_control_result_incomplete"
                    break
                if time.monotonic() >= deadline:
                    failure_class = "candidate_execution_timeout"
                    break
                if not frame_complete:
                    time.sleep(0.005)

            if frame_complete and failure_class is None:
                if not _wait_child_exit_without_reaping(
                    process.pid,
                    _CHILD_EXIT_GRACE_SECONDS,
                ):
                    failure_class = "candidate_process_did_not_exit"
            group_cleanup_ok = _terminate_candidate_group(process.pid)
            child_status, child_usage = _wait_child(
                process.pid,
                timeout=_CHILD_EXIT_GRACE_SECONDS,
            )
            if child_status is not None:
                process.returncode = os.waitstatus_to_exitcode(child_status)
            elif failure_class is None:
                failure_class = "candidate_process_reap_failed"
    except FoundryDemoContractError:
        raise
    except Exception as exc:
        failure_class = failure_class or f"candidate_runner_{exc.__class__.__name__}"
    finally:
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
        if process is not None and process.returncode is None:
            group_cleanup_ok = _terminate_candidate_group(process.pid) and group_cleanup_ok
            status, usage = _wait_child(
                process.pid,
                timeout=_CHILD_EXIT_GRACE_SECONDS,
            )
            if status is not None:
                child_status = status
                child_usage = child_usage or usage
                process.returncode = os.waitstatus_to_exitcode(status)
        for descriptor in tuple(open_descriptors):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        _drain_capture_threads(captures, tuple(threads))

    if not group_cleanup_ok and any(
        not capture.capture_complete for capture in captures
    ):
        failure_class = failure_class or "candidate_process_group_cleanup_failed"
    if any(not capture.capture_complete for capture in captures):
        failure_class = failure_class or "candidate_stream_capture_incomplete"
    if child_status is None:
        failure_class = failure_class or "candidate_child_status_missing"
    elif not os.WIFEXITED(child_status) or os.WEXITSTATUS(child_status) != 0:
        failure_class = failure_class or "candidate_child_exit_failed"
    outcome, control_failure = _decode_candidate_control(control_capture)
    failure_class = failure_class or control_failure
    if failure_class is not None:
        outcome = _failure_outcome(failure_class)
    if outcome is None:
        outcome = _failure_outcome("candidate_control_result_missing")
    return outcome, stdout_capture, stderr_capture, child_usage


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FoundryDemoContractError("candidate_payload_not_canonical_json") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _max_rss_kib(usage: Any) -> int | None:
    if usage is None:
        return None
    raw = int(usage.ru_maxrss)
    if platform.system() == "Darwin":
        return (raw + 1023) // 1024
    return raw


def validate_candidate_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy of one declarative, non-formal candidate."""

    if not isinstance(bundle, dict):
        raise FoundryDemoContractError("candidate_bundle_must_be_object")
    required = {
        "schema_version",
        "candidate_id",
        "candidate_version",
        "proposed_workflow_id",
        "entrypoint_id",
        "risk_level",
        "workflow_spec",
        "source_pins",
        "fixture_hashes",
        "dependency_lock_sha256",
        "runner_definition_sha256",
        "generation",
        "limitations",
        "output_policy",
    }
    if set(bundle) != required:
        raise FoundryDemoContractError("candidate_bundle_shape_not_registered")
    if bundle.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise FoundryDemoContractError("candidate_schema_version_unsupported")
    candidate_id = str(bundle.get("candidate_id") or "")
    workflow_id = str(bundle.get("proposed_workflow_id") or "")
    if not _CANDIDATE_ID.fullmatch(candidate_id):
        raise FoundryDemoContractError("candidate_id_invalid")
    if not _CANDIDATE_ID.fullmatch(workflow_id):
        raise FoundryDemoContractError("candidate_workflow_id_invalid")
    version = bundle.get("candidate_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise FoundryDemoContractError("candidate_version_invalid")
    if bundle.get("risk_level") not in {"R0", "R1", "R2", "R3"}:
        raise FoundryDemoContractError("candidate_risk_level_invalid")
    for field in ("dependency_lock_sha256", "runner_definition_sha256"):
        if not _HEX64.fullmatch(str(bundle.get(field) or "")):
            raise FoundryDemoContractError(f"candidate_{field}_invalid")
    if not isinstance(bundle.get("workflow_spec"), dict):
        raise FoundryDemoContractError("candidate_workflow_spec_invalid")
    if not isinstance(bundle.get("source_pins"), list) or not bundle["source_pins"]:
        raise FoundryDemoContractError("candidate_source_pins_required")
    if not isinstance(bundle.get("fixture_hashes"), list):
        raise FoundryDemoContractError("candidate_fixture_hashes_invalid")
    if not isinstance(bundle.get("generation"), dict):
        raise FoundryDemoContractError("candidate_generation_invalid")
    if not isinstance(bundle.get("limitations"), list) or not bundle["limitations"]:
        raise FoundryDemoContractError("candidate_limitations_required")
    output_policy = bundle.get("output_policy")
    if output_policy != {
        "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
    }:
        raise FoundryDemoContractError("candidate_output_policy_not_non_formal")
    if contains_formal_claim_escape(bundle):
        raise FoundryDemoContractError("candidate_bundle_formal_claim_escape")
    if str(bundle.get("entrypoint_id") or "") not in _ENTRYPOINTS:
        raise FoundryDemoContractError("candidate_entrypoint_not_allowlisted")
    return json.loads(_canonical_json(bundle))


def load_candidate_bundle(path: str | Path) -> dict[str, Any]:
    candidate_path = Path(path).resolve()
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoundryDemoContractError("candidate_bundle_unreadable") from exc
    return validate_candidate_bundle(payload)


def _run_desi_dr2_official_chain_summary(
    bundle: dict[str, Any],
    *,
    cache_root: str | Path | None,
) -> dict[str, Any]:
    from app.services.cosmology_likelihoods.analysis_registry import (
        audit_cosmology_analysis_registry,
    )
    from app.services.cosmology_likelihoods.dark_energy_matrix import (
        run_dark_energy_evidence_matrix,
    )

    issues = audit_cosmology_analysis_registry()
    if issues:
        return {
            "status": "FAILED",
            "failure_class": "official_registry_integrity_failed",
            "result": {"registry_issues": issues, "parameter_intervals": {}},
            "validation_summary": {
                "registry_integrity": False,
                "official_mirror_verified": False,
                "ready_cells": 0,
            },
        }

    workflow_spec = bundle["workflow_spec"]
    inputs = workflow_spec.get("demo_inputs") or {}
    prior_root = os.environ.get("DESI_DR2_OFFICIAL_CHAIN_ROOT")
    configured_root = cache_root if cache_root is not None else prior_root
    if cache_root is not None:
        os.environ["DESI_DR2_OFFICIAL_CHAIN_ROOT"] = str(cache_root)
    try:
        matrix = run_dark_energy_evidence_matrix(
            model=str(inputs.get("model") or "w0wa_cdm"),
            supernova_sets=list(inputs.get("supernova_sets") or ["union3"]),
            include_desi_dr1_reference=False,
        )
    finally:
        if cache_root is not None:
            if prior_root is None:
                os.environ.pop("DESI_DR2_OFFICIAL_CHAIN_ROOT", None)
            else:
                os.environ["DESI_DR2_OFFICIAL_CHAIN_ROOT"] = prior_root

    ready_cells = int(matrix.get("official_ready_cells") or 0)
    withheld_cells = int(matrix.get("official_withheld_cells") or 0)
    mirror_was_configured = bool(str(configured_root or "").strip())
    withheld_reasons = sorted(
        {
            str(reason)
            for cell in matrix.get("matrix") or []
            if isinstance(cell, dict)
            for reason in cell.get("withheld_reasons") or []
            if str(reason).strip()
        }
    )
    if ready_cells > 0 and withheld_cells == 0:
        status = "PASSED"
        failure_class = None
    elif mirror_was_configured:
        # A configured cache that is missing, corrupt, schema-incompatible, or
        # scientifically invalid is not equivalent to the operator declining
        # to provide a mirror.  Preserve the failed receipt, but make the public
        # wrapper return non-zero so an integrity failure cannot look like the
        # expected no-mirror demonstration.
        status = "FAILED"
        failure_class = "official_chain_mirror_integrity_failed"
    else:
        status = "PARTIAL"
        failure_class = "official_chain_mirror_unavailable"
    return {
        "status": status,
        "failure_class": failure_class,
        "result": {
            "analysis_status": matrix.get("analysis_status"),
            "official_ready_cells": ready_cells,
            "official_withheld_cells": withheld_cells,
            "matrix": matrix.get("matrix") or [],
            "provenance": matrix.get("provenance") or {},
            "parameter_intervals_are_non_formal": True,
        },
        "validation_summary": {
            "registry_integrity": True,
            "official_mirror_verified": ready_cells > 0,
            "official_mirror_configured": mirror_was_configured,
            "ready_cells": ready_cells,
            "withheld_cells": withheld_cells,
            "withheld_reasons": withheld_reasons,
            "numeric_claim_gate": "NON_FORMAL_DEMO",
        },
    }


def _run_generated_candidate_validation(
    bundle: dict[str, Any],
    *,
    cache_root: str | Path | None,
) -> dict[str, Any]:
    """Load one candidate module only inside the isolated Validation image.

    ``candidate_id`` is already constrained to lower-case identifier syntax.
    The fixed namespace prevents a bundle from selecting an arbitrary module.
    Formal Workers do not expose this entrypoint.
    """

    candidate_id = str(bundle["candidate_id"])
    module_name = f"app.services.foundry_generated.{candidate_id}"
    module = importlib.import_module(module_name)
    entrypoint = getattr(module, "run_demo", None)
    if not callable(entrypoint):
        raise FoundryDemoContractError("generated_candidate_run_demo_missing")
    result = entrypoint(bundle, cache_root=cache_root)
    if not isinstance(result, dict):
        raise FoundryDemoContractError("generated_candidate_result_not_object")
    return result


_ENTRYPOINTS: dict[
    str,
    Callable[..., dict[str, Any]],
] = {
    "desi_dr2_official_chain_summary_demo_v1": (
        _run_desi_dr2_official_chain_summary
    ),
    "candidate_generated_python_demo_v1": _run_generated_candidate_validation,
}


def _candidate_child_main(arguments: list[str]) -> int:
    """Trusted wrapper used only by the fresh Validation subprocess."""

    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--candidate-child", action="store_true", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--control-fd", required=True, type=int)
    args = parser.parse_args(arguments)
    control_fd = int(args.control_fd)
    exit_immediately = os._exit
    global _CANDIDATE_CHILD_ACTIVE
    try:
        os.set_inheritable(control_fd, False)
        request_path = Path(args.request)
        if request_path.stat().st_size > _CONTROL_RESULT_LIMIT_BYTES:
            raise FoundryDemoContractError("candidate_request_too_large")
        request = json.loads(request_path.read_bytes())
        if not isinstance(request, dict) or set(request) != {"bundle", "cache_root"}:
            raise FoundryDemoContractError("candidate_request_invalid")
        bundle = validate_candidate_bundle(request["bundle"])
        cache_root = request.get("cache_root")
        if cache_root is not None and not isinstance(cache_root, str):
            raise FoundryDemoContractError("candidate_cache_root_invalid")
        entrypoint = _ENTRYPOINTS[bundle["entrypoint_id"]]
        _CANDIDATE_CHILD_ACTIVE = True
        try:
            outcome = entrypoint(bundle, cache_root=cache_root)
            if not isinstance(outcome, dict):
                outcome = _failure_outcome("candidate_result_not_object")
        except BaseException as exc:
            outcome = _failure_outcome(exc.__class__.__name__)
        try:
            payload = _canonical_json(outcome)
        except FoundryDemoContractError:
            payload = _canonical_json(
                _failure_outcome("candidate_result_not_canonical_json")
            )
        if len(payload) > _CONTROL_RESULT_LIMIT_BYTES:
            payload = _canonical_json(
                _failure_outcome("candidate_control_result_too_large")
            )
        sys.stdout.flush()
        sys.stderr.flush()
        _write_all(control_fd, len(payload).to_bytes(8, "big") + payload)
        os.close(control_fd)
    except BaseException:
        with contextlib.suppress(BaseException):
            payload = _canonical_json(_failure_outcome("candidate_child_setup_failed"))
            _write_all(control_fd, len(payload).to_bytes(8, "big") + payload)
            os.close(control_fd)
    finally:
        exit_immediately(0)


def _normalize_candidate_outcome(outcome: Any) -> dict[str, Any]:
    required = {"status", "failure_class", "result", "validation_summary"}
    if not isinstance(outcome, dict) or set(outcome) != required:
        return _failure_outcome("candidate_demo_outcome_shape_invalid")
    status = outcome.get("status")
    if not isinstance(status, str) or status not in DEMO_STATUSES:
        return _failure_outcome("candidate_demo_status_invalid")
    failure_class = outcome.get("failure_class")
    if failure_class is not None and (
        not isinstance(failure_class, str) or not failure_class.strip()
    ):
        return _failure_outcome("candidate_demo_failure_class_invalid")
    if status == "PASSED" and failure_class is not None:
        return _failure_outcome("candidate_demo_failure_class_invalid")
    if status != "PASSED" and failure_class is None:
        return _failure_outcome("candidate_demo_failure_class_required")
    if not isinstance(outcome.get("result"), dict):
        return _failure_outcome("candidate_demo_result_invalid")
    if not isinstance(outcome.get("validation_summary"), dict):
        return _failure_outcome("candidate_demo_validation_summary_invalid")
    return outcome


def run_candidate_demo(
    bundle: dict[str, Any],
    *,
    cache_root: str | Path | None = None,
    runner_image_digest: str | None = None,
    candidate_version_sha256: str | None = None,
    started_at: datetime | None = None,
    captured_streams: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Run an allowlisted candidate and return an immutable DemoReport body."""

    normalized = validate_candidate_bundle(bundle)
    started = started_at or _utc_now()
    demo_run_id = str(uuid.uuid4())
    outcome, stdout_capture, stderr_capture, usage = (
        _execute_candidate_isolated(
            bundle=normalized,
            cache_root=cache_root,
        )
    )
    outcome = _normalize_candidate_outcome(outcome)
    status = outcome["status"]
    result = outcome["result"]
    stdout_bytes = stdout_capture.get_bytes()
    stderr_bytes = stderr_capture.get_bytes()
    stream_escape = contains_formal_claim_escape_text(
        stdout_bytes
    ) or contains_formal_claim_escape_text(stderr_bytes)
    stream_capture_incomplete = (
        not stdout_capture.capture_complete
        or not stderr_capture.capture_complete
    )
    stream_limit_exceeded = stdout_capture.truncated or stderr_capture.truncated
    candidate_output = {
        "failure_class": outcome.get("failure_class"),
        "result": result,
        "validation_summary": outcome.get("validation_summary"),
    }
    if contains_formal_claim_escape(
        candidate_output,
        scan_text_leaves=True,
    ) or stream_escape:
        status = "FAILED"
        result = {}
        outcome["failure_class"] = "candidate_formal_claim_escape_blocked"
        outcome["validation_summary"] = {"formal_claim_escape_blocked": True}
        if stream_escape:
            stdout_bytes = b""
            stderr_bytes = b"candidate stream quarantined: claim escape blocked\n"
    elif stream_limit_exceeded:
        status = "FAILED"
        result = {}
        outcome["failure_class"] = "candidate_stream_limit_exceeded"
        outcome["validation_summary"] = {"stream_limit_exceeded": True}
        stdout_bytes = b""
        stderr_bytes = b"candidate stream quarantined: capture limit exceeded\n"
    elif stream_capture_incomplete:
        status = "FAILED"
        result = {}
        upstream_failure = outcome.get("failure_class")
        outcome["failure_class"] = (
            upstream_failure or "candidate_stream_capture_incomplete"
        )
        outcome["validation_summary"] = {
            "stream_capture_complete": False,
            "upstream_failure_class": upstream_failure,
        }
        stdout_bytes = b""
        stderr_bytes = b"candidate stream quarantined: capture incomplete\n"
    completed = _utc_now()
    if captured_streams is not None:
        captured_streams.clear()
        captured_streams.update(
            {"stdout.log": stdout_bytes, "stderr.log": stderr_bytes}
        )
    environment = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "tool_version": str(
            os.getenv("TOOL_VERSION") or os.getenv("GIT_COMMIT") or "unknown"
        ),
        "entrypoint_id": normalized["entrypoint_id"],
    }
    report = {
        "schema_version": 1,
        "candidate_id": normalized["candidate_id"],
        "candidate_version": normalized["candidate_version"],
        "demo_run_id": demo_run_id,
        "status": status,
        "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
        "candidate_bundle_sha256": _sha256(normalized),
        "candidate_version_sha256": str(
            candidate_version_sha256 or _sha256(normalized)
        ),
        "workflow_spec_sha256": _sha256(normalized["workflow_spec"]),
        "dependency_lock_sha256": normalized["dependency_lock_sha256"],
        "runner_definition_sha256": normalized["runner_definition_sha256"],
        "runner_image_digest": str(runner_image_digest or "unavailable"),
        "environment": environment,
        "environment_sha256": _sha256(environment),
        "generation": normalized["generation"],
        "source_pins": normalized["source_pins"],
        "fixture_hashes": normalized["fixture_hashes"],
        "started_at": _iso(started),
        "completed_at": _iso(completed),
        "duration_ms": max(0, int((completed - started).total_seconds() * 1000)),
        "stdout_sha256": _sha256_bytes(stdout_bytes),
        "stderr_sha256": _sha256_bytes(stderr_bytes),
        "stdout_bytes": len(stdout_bytes),
        "stderr_bytes": len(stderr_bytes),
        "artifact_manifest": [
            {
                "path": "stdout.log",
                "kind": "STDOUT",
                "sha256": _sha256_bytes(stdout_bytes),
                "bytes": len(stdout_bytes),
            },
            {
                "path": "stderr.log",
                "kind": "STDERR",
                "sha256": _sha256_bytes(stderr_bytes),
                "bytes": len(stderr_bytes),
            },
        ],
        "resource_usage": {
            "max_rss_kib": _max_rss_kib(usage),
            "measurement_scope": "direct_candidate_subprocess_wait4",
            "user_cpu_seconds": (
                round(float(usage.ru_utime), 6) if usage is not None else None
            ),
            "system_cpu_seconds": (
                round(float(usage.ru_stime), 6) if usage is not None else None
            ),
            "stream_capture_limit_bytes": _STREAM_CAPTURE_LIMIT_BYTES,
            "stdout_observed_bytes": stdout_capture.observed_bytes,
            "stderr_observed_bytes": stderr_capture.observed_bytes,
            "stdout_truncated": stdout_capture.truncated,
            "stderr_truncated": stderr_capture.truncated,
        },
        "failure_class": outcome.get("failure_class"),
        "validation_summary": outcome.get("validation_summary") or {},
        "limitations": list(normalized["limitations"]),
        "result": result,
    }
    report["demo_report_sha256"] = _sha256(report)
    return report


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "DEMO_STATUSES",
    "FoundryDemoContractError",
    "NON_FORMAL_EVIDENCE_CLASS",
    "load_candidate_bundle",
    "run_candidate_demo",
    "validate_candidate_bundle",
]


if __name__ == "__main__":  # pragma: no cover - exercised through parent tests
    _candidate_child_main(sys.argv[1:])
