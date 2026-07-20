"""Local worker trust, key-storage, and command-surface regressions."""

from __future__ import annotations

import base64
import json
import stat
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.worker_contract import canonical_json
from app.services.registered_workflows import (
    UNION3_REPRODUCTION_WORKFLOW_ID,
    get_registered_dataset_pins,
)
from app.worker_agent.cli import build_parser
from app.worker_agent import cli as worker_cli
from app.worker_agent.client import (
    WorkerClientError,
    WorkerConfig,
    load_config,
    save_config,
    verify_task_envelope,
)


def _private_text(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")


def _public_text(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _config(control_key: Ed25519PrivateKey) -> WorkerConfig:
    return WorkerConfig(
        control_plane_url="https://control.example.test",
        worker_id=str(uuid.uuid4()),
        worker_name="test node",
        private_key=_private_text(Ed25519PrivateKey.generate()),
        protocol_version="1",
        task_signing_key_id="control-current",
        task_signing_public_key=_public_text(control_key),
    )


def test_worker_config_is_private_and_contains_no_platform_credentials(tmp_path):
    config = _config(Ed25519PrivateKey.generate())
    path = save_config(config, home=tmp_path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    text = path.read_text(encoding="utf-8")
    assert "jwt" not in text.lower()
    assert "redis" not in text.lower()
    assert "database" not in text.lower()
    assert "evidence" not in text.lower()
    assert load_config(home=tmp_path) == config

    path.chmod(0o644)
    with pytest.raises(WorkerClientError, match="0600"):
        load_config(home=tmp_path)


def test_worker_accepts_only_signed_registered_task_envelope():
    control_key = Ed25519PrivateKey.generate()
    config = _config(control_key)
    now = datetime.now(timezone.utc)
    envelope = {
        "protocol_version": "1",
        "job_id": "job-1",
        "audit_id": "audit-1",
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "a" * 64,
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
        "normalized_inputs": {},
        "input_sha256": "b" * 64,
        "dataset_pins": get_registered_dataset_pins(UNION3_REPRODUCTION_WORKFLOW_ID),
        "image_digest": "",
        "git_commit": "c" * 40,
        "resource_limits": {"cpu": 2, "memory_mb": 6144, "concurrency": 1},
        "deadline": (now + timedelta(minutes=30)).isoformat(),
        "lease_expires_at": (now + timedelta(minutes=2)).isoformat(),
    }
    signature = control_key.sign(canonical_json(envelope))
    signed = {
        **envelope,
        "server_signature": {
            "algorithm": "ed25519",
            "key_id": "control-current",
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }
    verify_task_envelope(config, signed)

    tampered = json.loads(json.dumps(signed))
    tampered["normalized_inputs"] = {"shell": "do-not-run"}
    with pytest.raises(WorkerClientError, match="signature is invalid"):
        verify_task_envelope(config, tampered)

    unregistered = dict(envelope)
    unregistered["workflow_key"] = "run_arbitrary_python"
    unregistered_signature = control_key.sign(canonical_json(unregistered))
    unregistered["server_signature"] = {
        "algorithm": "ed25519",
        "key_id": "control-current",
        "value": base64.b64encode(unregistered_signature).decode("ascii"),
    }
    with pytest.raises(WorkerClientError, match="unregistered workflow"):
        verify_task_envelope(config, unregistered)

    expired = dict(envelope)
    expired["lease_expires_at"] = (now - timedelta(seconds=1)).isoformat()
    expired["deadline"] = (now + timedelta(minutes=30)).isoformat()
    expired["server_signature"] = {
        "algorithm": "ed25519",
        "key_id": "control-current",
        "value": base64.b64encode(
            control_key.sign(
                canonical_json(
                    {k: v for k, v in expired.items() if k != "server_signature"}
                )
            )
        ).decode("ascii"),
    }
    with pytest.raises(WorkerClientError, match="expired"):
        verify_task_envelope(config, expired, now=now)

    wrong_pins = dict(envelope)
    wrong_pins["dataset_pins"] = []
    wrong_pins["server_signature"] = {
        "algorithm": "ed25519",
        "key_id": "control-current",
        "value": base64.b64encode(
            control_key.sign(
                canonical_json(
                    {k: v for k, v in wrong_pins.items() if k != "server_signature"}
                )
            )
        ).decode("ascii"),
    }
    with pytest.raises(WorkerClientError, match="dataset pins"):
        verify_task_envelope(config, wrong_pins, now=now)


def test_cli_surface_is_narrow_and_has_no_arbitrary_exec_command():
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "worker",
            "enroll",
            "ASTRO-WORKER-one-time-code",
            "--control-plane",
            "https://control.example.test",
        ]
    )
    assert parsed.command == "enroll"
    help_text = parser.format_help().lower()
    assert "shell" not in help_text
    assert "python" not in help_text


def test_worker_builds_and_uploads_registered_science_artifacts(monkeypatch):
    from app.services.union3_reproduction import run_union3_primary_reproduction

    control_key = Ed25519PrivateKey.generate()
    config = _config(control_key)
    envelope = {
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "a" * 64,
        "protocol_version": "1",
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
        "git_commit": "b" * 40,
        "image_digest": "sha256:" + "c" * 64,
    }
    result = run_union3_primary_reproduction()
    artifacts = worker_cli._science_artifacts(result, envelope)
    assert set(artifacts) == {
        "primary_analysis.json",
        "chi2_profile.svg",
        "environment.json",
    }
    assert (
        b"Union3 normalized profile chi-square curve"
        in artifacts["chi2_profile.svg"][0]
    )

    class Response:
        status_code = 200
        text = ""

        def __init__(self, payload=None):
            self._payload = payload or {}

        def json(self):
            return self._payload

    def issue(_config, *, payload, **_kwargs):
        uploads = [
            {
                "artifact_name": item["name"],
                "artifact_ref": f"science-attempts/test/{item['name']}",
                "url": f"https://objects.example.test/{item['name']}",
                "headers": {"Content-Length": str(item["size_bytes"])},
            }
            for item in payload["artifacts"]
        ]
        return Response({"uploads": uploads})

    uploaded: list[tuple[str, int]] = []

    def put(url, *, content, headers, timeout):
        assert int(headers["Content-Length"]) == len(content)
        assert timeout == 60.0
        uploaded.append((url, len(content)))
        return Response()

    monkeypatch.setattr(worker_cli, "signed_request", issue)
    monkeypatch.setattr(worker_cli.httpx, "put", put)
    manifest = worker_cli._upload_science_artifacts(
        config,
        envelope,
        result,
    )
    assert len(manifest) == 3
    assert len(uploaded) == 3


def test_worker_uses_cancel_ack_instead_of_failure_endpoint(monkeypatch):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {"attempt_id": str(uuid.uuid4()), "lease_id": "d" * 64}
    calls: list[str] = []

    class Response:
        status_code = 200

    def request(_config, *, path, **_kwargs):
        calls.append(path)
        return Response()

    monkeypatch.setattr(worker_cli, "signed_request", request)
    worker_cli._post_cancel_ack(config, envelope)
    assert calls == [f"/api/compute/v1/attempts/{envelope['attempt_id']}/cancel-ack"]


def test_heartbeat_503_is_classified_as_retryable(monkeypatch):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {"attempt_id": str(uuid.uuid4()), "lease_id": "9" * 64}

    class Response:
        status_code = 503

    monkeypatch.setattr(
        worker_cli,
        "signed_request",
        lambda *_args, **_kwargs: Response(),
    )
    heartbeat = worker_cli._LeaseHeartbeat(config, envelope)
    heartbeat._heartbeat_once()
    with pytest.raises(WorkerClientError) as caught:
        heartbeat.current_action()
    assert worker_cli._is_retryable_failure(caught.value) is True


def test_run_once_keeps_heartbeat_active_through_uploads_and_completion(
    monkeypatch,
    tmp_path,
):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "e" * 64,
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
    }
    result = {
        "workflow_id": "union3_flat_lcdm_sn_only_v1",
        "publication_ready": False,
    }
    events: list[str] = []
    statuses: list[dict] = []
    heartbeat_ref = {}

    class Response:
        status_code = 200
        text = ""

        def __init__(self, payload=None):
            self._payload = payload or {}

        def json(self):
            return self._payload

    class TrackingHeartbeat:
        def __init__(self, _config, _envelope):
            self.active = False
            heartbeat_ref["value"] = self

        def start(self):
            self.active = True
            events.append("heartbeat_started")

        def current_action(self):
            assert self.active
            return "continue"

        def stop_after_accepted_completion(self):
            assert self.active
            events.append("heartbeat_stopped")
            self.active = False

        def probe(self):
            return self.current_action()

        def stop_and_check(self):
            self.active = False
            return "continue"

    def request(_config, *, path, **_kwargs):
        if path == "/api/compute/v1/tasks/claim":
            return Response(envelope)
        if path.endswith("/complete"):
            assert heartbeat_ref["value"].active
            events.append("complete_while_heartbeat_active")
            return Response({"status": "SUCCEEDED"})
        raise AssertionError(f"Unexpected signed Worker request: {path}")

    def upload(_config, _envelope, _result, *, control_check):
        assert heartbeat_ref["value"].active
        assert control_check() == "continue"
        events.append("uploads_while_heartbeat_active")
        return [{"artifact_ref": "science-attempts/test/verified/result.json"}]

    monkeypatch.setattr(worker_cli, "load_config", lambda *, home: config)
    monkeypatch.setattr(worker_cli, "signed_request", request)
    monkeypatch.setattr(worker_cli, "verify_task_envelope", lambda *_args: None)
    monkeypatch.setattr(
        worker_cli, "save_status", lambda payload, **_kwargs: statuses.append(payload)
    )
    monkeypatch.setattr(worker_cli, "_LeaseHeartbeat", TrackingHeartbeat)
    monkeypatch.setattr(
        worker_cli, "_run_registered_workflow", lambda _envelope: result
    )
    monkeypatch.setattr(worker_cli, "_upload_science_artifacts", upload)

    assert worker_cli.run_once(home=tmp_path) is True
    assert events == [
        "heartbeat_started",
        "uploads_while_heartbeat_active",
        "complete_while_heartbeat_active",
        "heartbeat_stopped",
    ]
    assert statuses[-1]["last_completed_attempt"] == envelope["attempt_id"]


def test_run_once_server_cancel_acks_and_never_posts_failure(monkeypatch, tmp_path):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "f" * 64,
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
    }
    paths: list[str] = []
    statuses: list[dict] = []

    class Response:
        status_code = 200
        text = ""

        def __init__(self, payload=None):
            self._payload = payload or {}

        def json(self):
            return self._payload

    class CancelHeartbeat:
        def __init__(self, _config, _envelope):
            self.active = False

        def start(self):
            self.active = True

        def current_action(self):
            assert self.active
            return "cancel"

        def stop_and_check(self):
            self.active = False
            return "cancel"

    def request(_config, *, path, **_kwargs):
        paths.append(path)
        if path == "/api/compute/v1/tasks/claim":
            return Response(envelope)
        if path.endswith("/cancel-ack"):
            return Response({"status": "CANCELLED"})
        if path.endswith("/fail"):
            raise AssertionError(
                "A server-requested cancellation must never call /fail"
            )
        raise AssertionError(f"Unexpected signed Worker request: {path}")

    monkeypatch.setattr(worker_cli, "load_config", lambda *, home: config)
    monkeypatch.setattr(worker_cli, "signed_request", request)
    monkeypatch.setattr(worker_cli, "verify_task_envelope", lambda *_args: None)
    monkeypatch.setattr(
        worker_cli, "save_status", lambda payload, **_kwargs: statuses.append(payload)
    )
    monkeypatch.setattr(worker_cli, "_LeaseHeartbeat", CancelHeartbeat)
    monkeypatch.setattr(
        worker_cli,
        "_run_registered_workflow",
        lambda _envelope: {"publication_ready": False},
    )
    monkeypatch.setattr(
        worker_cli,
        "_upload_science_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Cancelled work must not upload artifacts")
        ),
    )

    assert worker_cli.run_once(home=tmp_path) is True
    assert paths == [
        "/api/compute/v1/tasks/claim",
        f"/api/compute/v1/attempts/{envelope['attempt_id']}/cancel-ack",
    ]
    assert statuses[-1]["last_control_action"] == "cancel"
    assert statuses[-1]["last_cancelled_attempt"] == envelope["attempt_id"]


def test_run_once_drain_releases_retryably_without_cancel_ack(monkeypatch, tmp_path):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "a" * 64,
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
    }
    calls: list[tuple[str, dict | None]] = []
    statuses: list[dict] = []

    class Response:
        status_code = 200
        text = ""

        def __init__(self, payload=None):
            self._payload = payload or {}

        def json(self):
            return self._payload

    class DrainHeartbeat:
        def __init__(self, _config, _envelope):
            self.active = False

        def start(self):
            self.active = True

        def current_action(self):
            assert self.active
            return "drain"

        def stop_and_check(self):
            self.active = False
            return "drain"

    def request(_config, *, path, payload=None, **_kwargs):
        calls.append((path, payload))
        if path == "/api/compute/v1/tasks/claim":
            return Response(envelope)
        if path.endswith("/fail"):
            assert payload["retryable"] is True
            assert payload["error_class"] == "worker_draining"
            return Response({"status": "FAILED"})
        if path.endswith("/cancel-ack"):
            raise AssertionError("Drain must not cancel the parent Audit")
        raise AssertionError(f"Unexpected signed Worker request: {path}")

    monkeypatch.setattr(worker_cli, "load_config", lambda *, home: config)
    monkeypatch.setattr(worker_cli, "signed_request", request)
    monkeypatch.setattr(worker_cli, "verify_task_envelope", lambda *_args: None)
    monkeypatch.setattr(
        worker_cli, "save_status", lambda payload, **_kwargs: statuses.append(payload)
    )
    monkeypatch.setattr(worker_cli, "_LeaseHeartbeat", DrainHeartbeat)
    monkeypatch.setattr(
        worker_cli,
        "_run_registered_workflow",
        lambda _envelope: {"publication_ready": False},
    )

    assert worker_cli.run_once(home=tmp_path) is True
    assert [path for path, _payload in calls] == [
        "/api/compute/v1/tasks/claim",
        f"/api/compute/v1/attempts/{envelope['attempt_id']}/fail",
    ]
    assert statuses[-1]["last_control_action"] == "drain"
    assert statuses[-1]["last_drained_attempt"] == envelope["attempt_id"]


def test_run_once_artifact_url_503_reports_retryable_failure(monkeypatch, tmp_path):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "b" * 64,
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
    }
    failure_payloads: list[dict] = []

    class Response:
        text = "temporarily unavailable"

        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class ContinueHeartbeat:
        def __init__(self, _config, _envelope):
            self.active = False

        def start(self):
            self.active = True

        def current_action(self):
            assert self.active
            return "continue"

        def probe(self):
            return self.current_action()

        def stop_and_check(self):
            self.active = False
            return "continue"

    def request(_config, *, path, payload=None, **_kwargs):
        if path == "/api/compute/v1/tasks/claim":
            return Response(200, envelope)
        if path.endswith("/artifact-urls"):
            return Response(503)
        if path.endswith("/fail"):
            failure_payloads.append(payload)
            return Response(200, {"status": "FAILED"})
        if path.endswith("/complete"):
            raise AssertionError("A failed upload handshake cannot be completed")
        raise AssertionError(f"Unexpected signed Worker request: {path}")

    monkeypatch.setattr(worker_cli, "load_config", lambda *, home: config)
    monkeypatch.setattr(worker_cli, "signed_request", request)
    monkeypatch.setattr(worker_cli, "verify_task_envelope", lambda *_args: None)
    monkeypatch.setattr(worker_cli, "save_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_cli, "_LeaseHeartbeat", ContinueHeartbeat)
    monkeypatch.setattr(
        worker_cli,
        "_run_registered_workflow",
        lambda _envelope: {"publication_ready": False},
    )
    monkeypatch.setattr(
        worker_cli,
        "_science_artifacts",
        lambda _result, _envelope: {
            "primary_analysis.json": (b"{}\n", "application/json")
        },
    )

    with pytest.raises(WorkerClientError, match="Artifact URL request failed"):
        worker_cli.run_once(home=tmp_path)
    assert len(failure_payloads) == 1
    assert failure_payloads[0]["retryable"] is True
    assert failure_payloads[0]["lease_id"] == envelope["lease_id"]


def test_run_once_object_upload_503_reports_retryable_failure(monkeypatch, tmp_path):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "c" * 64,
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
    }
    failure_payloads: list[dict] = []

    class Response:
        text = "service unavailable"

        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class ContinueHeartbeat:
        def __init__(self, _config, _envelope):
            self.active = False

        def start(self):
            self.active = True

        def current_action(self):
            assert self.active
            return "continue"

        def probe(self):
            return self.current_action()

        def stop_and_check(self):
            self.active = False
            return "continue"

    def request(_config, *, path, payload=None, **_kwargs):
        if path == "/api/compute/v1/tasks/claim":
            return Response(200, envelope)
        if path.endswith("/artifact-urls"):
            artifact = payload["artifacts"][0]
            return Response(
                200,
                {
                    "uploads": [
                        {
                            "artifact_name": artifact["name"],
                            "artifact_ref": "science-attempts/test/upload.json",
                            "url": "https://objects.example.test/upload.json",
                            "headers": {},
                        }
                    ]
                },
            )
        if path.endswith("/fail"):
            failure_payloads.append(payload)
            return Response(200, {"status": "FAILED"})
        if path.endswith("/complete"):
            raise AssertionError("A failed object upload cannot be completed")
        raise AssertionError(f"Unexpected signed Worker request: {path}")

    monkeypatch.setattr(worker_cli, "load_config", lambda *, home: config)
    monkeypatch.setattr(worker_cli, "signed_request", request)
    monkeypatch.setattr(worker_cli, "verify_task_envelope", lambda *_args: None)
    monkeypatch.setattr(worker_cli, "save_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_cli, "_LeaseHeartbeat", ContinueHeartbeat)
    monkeypatch.setattr(
        worker_cli,
        "_run_registered_workflow",
        lambda _envelope: {"publication_ready": False},
    )
    monkeypatch.setattr(
        worker_cli,
        "_science_artifacts",
        lambda _result, _envelope: {
            "primary_analysis.json": (b"{}\n", "application/json")
        },
    )
    monkeypatch.setattr(
        worker_cli.httpx,
        "put",
        lambda *_args, **_kwargs: Response(503),
    )

    with pytest.raises(WorkerClientError, match="Artifact upload failed"):
        worker_cli.run_once(home=tmp_path)
    assert len(failure_payloads) == 1
    assert failure_payloads[0]["retryable"] is True
    assert failure_payloads[0]["lease_id"] == envelope["lease_id"]


def test_run_once_completion_503_reports_retryable_failure(monkeypatch, tmp_path):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "d" * 64,
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
    }
    failure_payloads: list[dict] = []

    class Response:
        text = "service unavailable"

        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class ContinueHeartbeat:
        def __init__(self, _config, _envelope):
            self.active = False

        def start(self):
            self.active = True

        def current_action(self):
            assert self.active
            return "continue"

        def probe(self):
            return self.current_action()

        def stop_and_check(self):
            self.active = False
            return "continue"

    def request(_config, *, path, payload=None, **_kwargs):
        if path == "/api/compute/v1/tasks/claim":
            return Response(200, envelope)
        if path.endswith("/complete"):
            return Response(503)
        if path.endswith("/fail"):
            failure_payloads.append(payload)
            return Response(200, {"status": "FAILED"})
        raise AssertionError(f"Unexpected signed Worker request: {path}")

    monkeypatch.setattr(worker_cli, "load_config", lambda *, home: config)
    monkeypatch.setattr(worker_cli, "signed_request", request)
    monkeypatch.setattr(worker_cli, "verify_task_envelope", lambda *_args: None)
    monkeypatch.setattr(worker_cli, "save_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_cli, "_LeaseHeartbeat", ContinueHeartbeat)
    monkeypatch.setattr(
        worker_cli,
        "_run_registered_workflow",
        lambda _envelope: {"publication_ready": False},
    )
    monkeypatch.setattr(
        worker_cli,
        "_upload_science_artifacts",
        lambda *_args, **_kwargs: [
            {"artifact_ref": "science-attempts/test/verified/result.json"}
        ],
    )

    with pytest.raises(WorkerClientError, match="Task completion failed"):
        worker_cli.run_once(home=tmp_path)
    assert len(failure_payloads) == 1
    assert failure_payloads[0]["retryable"] is True
    assert failure_payloads[0]["lease_id"] == envelope["lease_id"]
