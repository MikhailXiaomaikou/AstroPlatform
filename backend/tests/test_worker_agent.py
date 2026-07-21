"""Local worker trust, key-storage, and command-surface regressions."""

from __future__ import annotations

import base64
import json
import stat
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.worker_contract import WORKER_PROTOCOL_VERSION, canonical_json
from app.services.registered_workflows import (
    UNION3_REPRODUCTION_WORKFLOW_ID,
    get_registered_dataset_pins,
)
from app.services.workflow_registry_v2 import get_worker_execution_binding
from app.worker_agent.cli import build_parser
from app.worker_agent import cli as worker_cli
from app.worker_agent.client import (
    WorkerClientError,
    WorkerConfig,
    enroll,
    load_config,
    save_config,
    signed_request,
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


@pytest.mark.parametrize(
    ("control_plane_url", "expected_base_url", "expected_trust_env"),
    [
        ("https://control.example.test/", "https://control.example.test", True),
        (
            "HTTPS://Control.Example.Test:443/",
            "https://control.example.test:443",
            True,
        ),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000", False),
        ("http://127.42.7.9", "http://127.42.7.9", False),
        ("http://[::1]:8000/", "http://[::1]:8000", False),
    ],
)
def test_enrollment_accepts_https_or_exact_loopback_origin(
    monkeypatch,
    tmp_path,
    control_plane_url: str,
    expected_base_url: str,
    expected_trust_env: bool,
):
    control_key = Ed25519PrivateKey.generate()
    node_id = uuid.uuid4()
    requested_urls: list[str] = []

    def fake_post(url, **kwargs):
        requested_urls.append(url)
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is expected_trust_env
        return SimpleNamespace(
            status_code=201,
            text="",
            json=lambda: {
                "node_id": str(node_id),
                "task_signing_key": {
                    "algorithm": "ed25519",
                    "key_id": "control-current",
                    "public_key": _public_text(control_key),
                },
            },
        )

    monkeypatch.setattr("app.worker_agent.client.httpx.post", fake_post)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")

    config = enroll(
        control_plane_url=control_plane_url,
        enrollment_code="ASTRO-WORKER-test-code",
        worker_name="URL validation node",
        home=tmp_path,
    )

    assert config.control_plane_url == expected_base_url
    assert requested_urls == [expected_base_url + "/api/compute/v1/nodes/enroll"]


@pytest.mark.parametrize(
    "control_plane_url",
    [
        "http://127.0.0.1.evil.example",
        "http://127.0.0.1@evil.example",
        "https://user:password@control.example.test",
        "http://attacker@127.0.0.1",
        "http://localhost",
        "http://localhost.evil.example",
        "http://192.168.1.1",
        "http://127.1",
        "http://2130706433",
        "http://0177.0.0.1",
        "http://0x7f000001",
        "http://127.0.0.1.",
        "http://[::2]",
        "http://[::1%25lo0]",
        "http://[::ffff:127.0.0.1]",
        "https://control.example.test/base-path",
        "https://control.example.test?next=https://evil.example",
        "https://control.example.test#fragment",
        "https://control.example.test:0",
        "https://control.example.test:",
        "https://control.example.test:invalid",
        "ftp://control.example.test",
    ],
)
def test_enrollment_rejects_ambiguous_or_non_loopback_http_origin(
    monkeypatch,
    tmp_path,
    control_plane_url: str,
):
    def unexpected_post(*_args, **_kwargs):
        raise AssertionError("an invalid control-plane URL must not reach the network")

    monkeypatch.setattr("app.worker_agent.client.httpx.post", unexpected_post)

    with pytest.raises(WorkerClientError):
        enroll(
            control_plane_url=control_plane_url,
            enrollment_code="ASTRO-WORKER-test-code",
            worker_name="Rejected URL node",
            home=tmp_path,
        )


def test_enrollment_redirect_does_not_persist_worker_credentials(
    monkeypatch,
    tmp_path,
):
    def redirect(_url, **kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return SimpleNamespace(
            status_code=302,
            text="redirect denied",
            json=lambda: (_ for _ in ()).throw(
                AssertionError("redirect response JSON must not be trusted")
            ),
        )

    monkeypatch.setattr("app.worker_agent.client.httpx.post", redirect)

    with pytest.raises(WorkerClientError, match=r"Enrollment failed \(302\)"):
        enroll(
            control_plane_url="http://127.0.0.1:8000",
            enrollment_code="ASTRO-WORKER-test-code",
            worker_name="Redirected node",
            home=tmp_path,
        )

    assert not (tmp_path / "node.json").exists()
    assert not (tmp_path / "status.json").exists()


def test_saved_and_loaded_configs_revalidate_control_plane_origin(tmp_path):
    safe = _config(Ed25519PrivateKey.generate())
    unsafe = replace(
        safe,
        control_plane_url="http://127.0.0.1.evil.example",
    )
    with pytest.raises(WorkerClientError):
        save_config(unsafe, home=tmp_path)
    assert not (tmp_path / "node.json").exists()

    path = tmp_path / "node.json"
    path.write_text(
        json.dumps(
            {
                **safe.__dict__,
                "control_plane_url": "http://attacker@127.0.0.1",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    with pytest.raises(WorkerClientError):
        load_config(home=tmp_path)


def test_signed_request_revalidates_legacy_config_and_disables_loopback_proxy(
    monkeypatch,
):
    safe = _config(Ed25519PrivateKey.generate())
    loopback = replace(safe, control_plane_url="http://127.0.0.1:8000")
    observed: dict[str, object] = {}

    def request(method, url, **kwargs):
        observed.update(method=method, url=url, **kwargs)
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr("app.worker_agent.client.httpx.request", request)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")

    signed_request(
        loopback,
        method="POST",
        path="/api/compute/v1/tasks/claim",
    )

    assert observed["url"] == "http://127.0.0.1:8000/api/compute/v1/tasks/claim"
    assert observed["trust_env"] is False
    assert observed["follow_redirects"] is False

    unsafe = replace(
        safe,
        control_plane_url="http://127.0.0.1.evil.example",
    )
    observed.clear()
    with pytest.raises(WorkerClientError):
        signed_request(
            unsafe,
            method="POST",
            path="/api/compute/v1/tasks/claim",
        )
    assert observed == {}


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


def test_v2_worker_rejects_non_static_adapter_binding():
    control_key = Ed25519PrivateKey.generate()
    legacy_config = _config(control_key)
    config = replace(legacy_config, protocol_version=WORKER_PROTOCOL_VERSION)
    binding = get_worker_execution_binding(UNION3_REPRODUCTION_WORKFLOW_ID)
    now = datetime.now(timezone.utc)
    envelope = {
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "job_id": "job-v2",
        "audit_id": "audit-v2",
        "attempt_id": str(uuid.uuid4()),
        "lease_id": "a" * 64,
        "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
        "workflow_version": binding["workflow_version"],
        "registry_epoch": binding["registry_epoch"],
        "registry_entry_hash": binding["registry_entry_hash"],
        "entrypoint_id": binding["entrypoint_id"],
        "execution_adapter_id": binding["execution_adapter_id"],
        "tool_spec_hash": binding["tool_spec_hash"],
        "normalized_inputs": {},
        "input_sha256": "b" * 64,
        "dataset_pins": get_registered_dataset_pins(
            UNION3_REPRODUCTION_WORKFLOW_ID
        ),
        "worker_image_digest": "unknown",
        "git_commit": "c" * 40,
        "resource_limits": {"cpu": 2, "memory_mb": 6144, "concurrency": 1},
        "deadline": (now + timedelta(minutes=30)).isoformat(),
        "lease_expires_at": (now + timedelta(minutes=2)).isoformat(),
    }

    def signed(value):
        return {
            **value,
            "server_signature": {
                "algorithm": "ed25519",
                "key_id": "control-current",
                "value": base64.b64encode(
                    control_key.sign(canonical_json(value))
                ).decode("ascii"),
            },
        }

    verify_task_envelope(config, signed(envelope), now=now)
    incompatible = {**envelope, "tool_spec_hash": "sha256:" + "0" * 64}
    with pytest.raises(WorkerClientError, match="image-static"):
        verify_task_envelope(config, signed(incompatible), now=now)


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


def test_heartbeat_409_server_cancellation_becomes_cancel_action(monkeypatch):
    config = _config(Ed25519PrivateKey.generate())
    envelope = {"attempt_id": str(uuid.uuid4()), "lease_id": "8" * 64}

    class Response:
        status_code = 409

        @staticmethod
        def json():
            return {"detail": "science_job_cancelled"}

    monkeypatch.setattr(
        worker_cli,
        "signed_request",
        lambda *_args, **_kwargs: Response(),
    )
    heartbeat = worker_cli._LeaseHeartbeat(config, envelope)
    heartbeat._heartbeat_once()
    assert heartbeat.current_action() == "cancel"


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
