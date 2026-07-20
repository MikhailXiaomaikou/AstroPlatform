"""Command-line entry point for the outbound-only local science worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.services.worker_contract import canonical_result_hash
from app.worker_agent.client import (
    WorkerClientError,
    config_path,
    enroll,
    load_config,
    save_status,
    signed_request,
    state_path,
    verify_task_envelope,
)

_HEARTBEAT_INTERVAL_SECONDS = 30.0
_SERVER_CANCELLATION_DETAILS = {
    "science_job_cancelled",
    "claim_audit_cancelled",
}


class _WorkerControlStop(RuntimeError):
    """A signed control-plane instruction to stop the current attempt."""

    def __init__(self, action: str) -> None:
        super().__init__(f"Worker received control action: {action}")
        self.action = action


class _RetryableWorkerError(WorkerClientError):
    """A transient control-plane or object-storage failure."""


def _transient_http_status(status_code: int) -> bool:
    return status_code in {408, 425, 429} or status_code >= 500


def _is_retryable_failure(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(
            current,
            (
                _RetryableWorkerError,
                httpx.TransportError,
                TimeoutError,
            ),
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


class _LeaseHeartbeat:
    """Renew one short worker lease while a fixed workflow is running."""

    def __init__(self, config, envelope: dict) -> None:
        self._config = config
        self._envelope = envelope
        self._stop = threading.Event()
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._error: Exception | None = None
        self._action = "continue"
        self._thread = threading.Thread(
            target=self._run,
            name="standard-astro-worker-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def current_action(self) -> str:
        """Return the latest server action without stopping lease renewal."""

        with self._state_lock:
            error = self._error
            action = self._action
        if error is not None:
            raise WorkerClientError(
                "Worker heartbeat failed; result was withheld"
            ) from error
        return action

    def probe(self) -> str:
        """Synchronously refresh the lease before reporting a local failure."""

        if self.current_action() != "continue":
            return self.current_action()
        self._heartbeat_once()
        return self.current_action()

    def stop_and_check(self) -> str:
        self._stop.set()
        self._thread.join(timeout=20.0)
        if self._thread.is_alive():
            raise WorkerClientError("Worker heartbeat did not stop safely")
        return self.current_action()

    def stop_after_accepted_completion(self) -> None:
        """Stop after a 2xx completion response without reclassifying success.

        The in-flight heartbeat can legitimately receive
        ``science_attempt_not_active`` immediately after ``/complete`` commits.
        The accepted completion is authoritative, so that terminal heartbeat
        response must not trigger a false ``/fail`` report.
        """

        self._stop.set()
        self._thread.join(timeout=20.0)
        if self._thread.is_alive():
            raise WorkerClientError("Worker heartbeat did not stop safely")

    def _heartbeat_once(self) -> None:
        with self._request_lock:
            if self._stop.is_set():
                return
            try:
                response = signed_request(
                    self._config,
                    method="PUT",
                    path=(
                        "/api/compute/v1/attempts/"
                        f"{self._envelope['attempt_id']}/heartbeat"
                    ),
                    payload={
                        "lease_id": self._envelope["lease_id"],
                        "progress": None,
                        "checkpoint": {
                            "phase": "primary_reproduction",
                            "restartable": False,
                        },
                    },
                    timeout=15.0,
                )
                if response.status_code >= 400:
                    error_type = (
                        _RetryableWorkerError
                        if _transient_http_status(response.status_code)
                        else WorkerClientError
                    )
                    raise error_type(f"Heartbeat was rejected ({response.status_code})")
                payload = response.json()
                action = str(payload.get("action") or "")
                if action not in {"continue", "cancel", "drain"}:
                    raise WorkerClientError("Heartbeat returned an invalid action")
                with self._state_lock:
                    self._action = action
            except Exception as exc:  # noqa: BLE001 - crossing a thread boundary
                with self._state_lock:
                    self._error = exc

    def _run(self) -> None:
        while not self._stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
            self._heartbeat_once()
            try:
                action = self.current_action()
            except WorkerClientError:
                return
            if action != "continue":
                return


def _require_continue(control_check: Callable[[], str]) -> None:
    action = control_check()
    if action != "continue":
        raise _WorkerControlStop(action)


def _server_cancellation_action(response: httpx.Response) -> str | None:
    if response.status_code != 409:
        return None
    try:
        detail = response.json().get("detail")
    except (AttributeError, TypeError, ValueError):
        return None
    return "cancel" if detail in _SERVER_CANCELLATION_DETAILS else None


def _run_registered_workflow(envelope: dict) -> dict:
    if envelope.get("workflow_key") != "union3_flat_lcdm_sn_only_v1":
        raise WorkerClientError("Only the registered Union3 workflow is executable")
    from app.services.union3_reproduction import run_union3_primary_reproduction

    result = run_union3_primary_reproduction()
    if result.get("publication_ready") is not False:
        raise WorkerClientError(
            "Registered workflow violated publication-readiness policy"
        )
    return result


def _post_failure(
    config,
    envelope: dict,
    error: Exception,
    *,
    retryable: bool = False,
    error_class: str | None = None,
) -> None:
    attempt_id = envelope.get("attempt_id")
    lease_id = envelope.get("lease_id")
    if not attempt_id or not lease_id:
        return
    response = signed_request(
        config,
        method="POST",
        path=f"/api/compute/v1/attempts/{attempt_id}/fail",
        payload={
            "lease_id": lease_id,
            "error_class": (error_class or type(error).__name__)[:255],
            "retryable": retryable,
        },
    )
    cancellation_action = _server_cancellation_action(response)
    if cancellation_action is not None:
        raise _WorkerControlStop(cancellation_action)
    if response.status_code >= 400:
        raise WorkerClientError(
            f"Could not report controlled failure ({response.status_code})"
        )


def _post_cancel_ack(config, envelope: dict) -> None:
    response = signed_request(
        config,
        method="POST",
        path=f"/api/compute/v1/attempts/{envelope['attempt_id']}/cancel-ack",
        payload={"lease_id": envelope["lease_id"]},
    )
    if response.status_code >= 400:
        raise WorkerClientError(
            f"Could not acknowledge cancellation ({response.status_code})"
        )


def _finish_control_action(
    config,
    envelope: dict,
    action: str,
    *,
    home: Path | None,
) -> None:
    if action == "cancel":
        _post_cancel_ack(config, envelope)
        status_payload = {
            "last_cancelled_attempt": envelope["attempt_id"],
        }
    elif action == "drain":
        # Draining is a node lifecycle event, not cancellation of the user's
        # Audit. Release the attempt through the retryable failure transition
        # so PostgreSQL requeues it for another eligible node.
        try:
            _post_failure(
                config,
                envelope,
                _WorkerControlStop(action),
                retryable=True,
                error_class="worker_draining",
            )
        except _WorkerControlStop as exc:
            # An authenticated owner cancellation can win the race with a
            # node entering drain mode. It takes precedence over requeueing.
            _finish_control_action(config, envelope, exc.action, home=home)
            return
        status_payload = {
            "last_drained_attempt": envelope["attempt_id"],
        }
    else:
        raise WorkerClientError(f"Unsupported worker control action: {action}")
    save_status(
        {
            "state": "idle",
            "worker_id": config.worker_id,
            **status_payload,
            "last_control_action": action,
            "last_error": None,
        },
        home=home,
    )


def _science_artifacts(result: dict, envelope: dict) -> dict[str, tuple[bytes, str]]:
    from app.services.union3_research_loop import _union3_profile_svg

    primary_json = (
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    environment = {
        "schema_version": "standard_astro_worker_environment_v1",
        "protocol_version": envelope["protocol_version"],
        "workflow_key": envelope["workflow_key"],
        "git_commit": envelope["git_commit"],
        "image_digest": envelope["image_digest"],
        "python_version": platform.python_version(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "mcmc": "not_applicable",
    }
    environment_json = (
        json.dumps(
            environment,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    chart = _union3_profile_svg(result).encode("utf-8")
    return {
        "primary_analysis.json": (primary_json, "application/json"),
        "chi2_profile.svg": (chart, "image/svg+xml"),
        "environment.json": (environment_json, "application/json"),
    }


def _upload_science_artifacts(
    config,
    envelope: dict,
    result: dict,
    *,
    control_check: Callable[[], str] = lambda: "continue",
) -> list[dict[str, str]]:
    artifacts = _science_artifacts(result, envelope)
    declarations = [
        {
            "name": name,
            "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "content_type": content_type,
        }
        for name, (payload, content_type) in artifacts.items()
    ]
    _require_continue(control_check)
    issued = signed_request(
        config,
        method="POST",
        path=f"/api/compute/v1/attempts/{envelope['attempt_id']}/artifact-urls",
        payload={"lease_id": envelope["lease_id"], "artifacts": declarations},
    )
    cancellation_action = _server_cancellation_action(issued)
    if cancellation_action is not None:
        raise _WorkerControlStop(cancellation_action)
    if issued.status_code >= 400:
        error_type = (
            _RetryableWorkerError
            if _transient_http_status(issued.status_code)
            else WorkerClientError
        )
        raise error_type(
            f"Artifact URL request failed ({issued.status_code}): {issued.text[:500]}"
        )
    _require_continue(control_check)
    upload_records = issued.json().get("uploads")
    if not isinstance(upload_records, list) or len(upload_records) != len(artifacts):
        raise WorkerClientError(
            "Control plane returned an invalid artifact upload batch"
        )
    supplied: list[dict[str, str]] = []
    for upload in upload_records:
        _require_continue(control_check)
        name = str(upload.get("artifact_name") or "")
        if name not in artifacts:
            raise WorkerClientError("Artifact upload batch changed a registered name")
        payload, _content_type = artifacts[name]
        try:
            response = httpx.put(
                str(upload["url"]),
                content=payload,
                headers=dict(upload.get("headers") or {}),
                timeout=60.0,
            )
        except httpx.TransportError as exc:
            raise _RetryableWorkerError(
                f"Artifact upload transport failed for {name}"
            ) from exc
        if response.status_code >= 400:
            error_type = (
                _RetryableWorkerError
                if _transient_http_status(response.status_code)
                else WorkerClientError
            )
            raise error_type(
                f"Artifact upload failed ({response.status_code}) for {name}"
            )
        _require_continue(control_check)
        supplied.append({"artifact_ref": str(upload["artifact_ref"])})
    return supplied


def run_once(*, home: Path | None = None) -> bool:
    config = load_config(home=home)
    response = signed_request(
        config,
        method="POST",
        path="/api/compute/v1/tasks/claim",
        query="?wait_seconds=25",
        timeout=35.0,
    )
    if response.status_code == 204:
        save_status(
            {
                "state": "idle",
                "worker_id": config.worker_id,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "last_error": None,
            },
            home=home,
        )
        return False
    if response.status_code >= 400:
        raise WorkerClientError(
            f"Task claim failed ({response.status_code}): {response.text[:500]}"
        )
    envelope = response.json()
    verify_task_envelope(config, envelope)
    save_status(
        {
            "state": "running",
            "worker_id": config.worker_id,
            "attempt_id": envelope["attempt_id"],
            "workflow_key": envelope["workflow_key"],
            "last_error": None,
        },
        home=home,
    )
    heartbeat = _LeaseHeartbeat(config, envelope)
    heartbeat.start()
    try:
        result = _run_registered_workflow(envelope)
        _require_continue(heartbeat.current_action)
        artifact_manifest = _upload_science_artifacts(
            config,
            envelope,
            result,
            control_check=heartbeat.current_action,
        )
        _require_continue(heartbeat.current_action)
        result_hash = canonical_result_hash(result)
        complete = signed_request(
            config,
            method="POST",
            path=f"/api/compute/v1/attempts/{envelope['attempt_id']}/complete",
            payload={
                "lease_id": envelope["lease_id"],
                "result": result,
                "result_hash": "sha256:" + result_hash,
                "diagnostics": {
                    "mcmc": "not_applicable",
                    "artifact_count": len(artifact_manifest),
                },
                "artifacts": artifact_manifest,
            },
        )
        cancellation_action = _server_cancellation_action(complete)
        if cancellation_action is not None:
            raise _WorkerControlStop(cancellation_action)
        if complete.status_code >= 400:
            error_type = (
                _RetryableWorkerError
                if _transient_http_status(complete.status_code)
                else WorkerClientError
            )
            raise error_type(
                f"Task completion failed ({complete.status_code}): {complete.text[:500]}"
            )
        # Keep the renewal thread alive until the completion response arrives.
        # A concurrent terminal heartbeat response after that 2xx is harmless.
        heartbeat.stop_after_accepted_completion()
    except _WorkerControlStop as exc:
        try:
            heartbeat.stop_and_check()
        except WorkerClientError:
            # A direct 409 cancellation response is already authoritative.
            pass
        _finish_control_action(config, envelope, exc.action, home=home)
        return True
    except Exception as exc:
        try:
            control_action = heartbeat.probe()
        except WorkerClientError:
            control_action = "continue"
        try:
            stopped_action = heartbeat.stop_and_check()
            if stopped_action != "continue":
                control_action = stopped_action
        except WorkerClientError:
            pass
        if control_action != "continue":
            _finish_control_action(config, envelope, control_action, home=home)
            return True
        try:
            _post_failure(
                config,
                envelope,
                exc,
                retryable=_is_retryable_failure(exc),
            )
        except _WorkerControlStop as control_stop:
            _finish_control_action(
                config,
                envelope,
                control_stop.action,
                home=home,
            )
            return True
        save_status(
            {
                "state": "failed",
                "worker_id": config.worker_id,
                "attempt_id": envelope.get("attempt_id"),
                "last_error": type(exc).__name__,
            },
            home=home,
        )
        raise
    save_status(
        {
            "state": "idle",
            "worker_id": config.worker_id,
            "last_completed_attempt": envelope["attempt_id"],
            "last_result_hash": "sha256:" + result_hash,
            "last_error": None,
        },
        home=home,
    )
    return True


def _status(home: Path | None) -> int:
    config = load_config(home=home)
    path = state_path(home)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"state": "unknown"}
    print(
        json.dumps(
            {
                "worker_id": config.worker_id,
                "worker_name": config.worker_name,
                "control_plane_url": config.control_plane_url,
                "config_path": str(config_path(home)),
                **state,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _verify_evidence(pack_path: Path, keyring_path: Path | None) -> int:
    from app.services.evidence_pack_v2 import verify_evidence_pack_v2

    trusted_path = keyring_path or (
        Path.home() / ".standard-astro" / "evidence-keys.json"
    )
    try:
        pack = pack_path.read_bytes()
        keyring = json.loads(trusted_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkerClientError(
            "Offline verification needs a previously trusted keyring; pass "
            "--keyring or save it at ~/.standard-astro/evidence-keys.json"
        ) from exc
    except json.JSONDecodeError as exc:
        raise WorkerClientError("The trusted Evidence keyring is invalid JSON") from exc
    result = verify_evidence_pack_v2(pack, trusted_keyring=keyring)
    print(
        json.dumps(
            {
                "valid": result.valid,
                "code": result.code,
                "key_id": result.key_id,
                "key_status": result.key_status,
                "audit_id": (result.manifest or {}).get("audit_id"),
                "warning": (
                    "A valid signature proves origin and integrity; it does not "
                    "by itself prove scientific correctness."
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astro", description="Standard Astro CLI")
    subcommands = parser.add_subparsers(dest="group", required=True)
    worker = subcommands.add_parser("worker", help="Manage a local science worker")
    worker_commands = worker.add_subparsers(dest="command", required=True)

    enroll_parser = worker_commands.add_parser("enroll")
    enroll_parser.add_argument("one_time_code")
    enroll_parser.add_argument("--control-plane", required=True)
    enroll_parser.add_argument("--name", default="Standard Astro local worker")
    enroll_parser.add_argument("--home", type=Path)

    start_parser = worker_commands.add_parser("start")
    start_parser.add_argument("--once", action="store_true")
    start_parser.add_argument("--home", type=Path)

    status_parser = worker_commands.add_parser("status")
    status_parser.add_argument("--home", type=Path)

    evidence = subcommands.add_parser("evidence", help="Verify scientific evidence")
    evidence_commands = evidence.add_subparsers(dest="command", required=True)
    verify_parser = evidence_commands.add_parser("verify")
    verify_parser.add_argument("pack", type=Path)
    verify_parser.add_argument(
        "--keyring",
        type=Path,
        help="Trusted keyring downloaded from the official .well-known endpoint",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.group == "evidence" and args.command == "verify":
            return _verify_evidence(args.pack, args.keyring)
        if args.command == "enroll":
            config = enroll(
                control_plane_url=args.control_plane,
                enrollment_code=args.one_time_code,
                worker_name=args.name,
                home=args.home,
            )
            print(
                f"Enrolled worker {config.worker_id}; the one-time code was not saved."
            )
            return 0
        if args.command == "status":
            return _status(args.home)
        if args.command == "start":
            while True:
                run_once(home=args.home)
                if args.once:
                    return 0
                time.sleep(1)
    except (WorkerClientError, OSError, ValueError) as exc:
        print(f"astro: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
