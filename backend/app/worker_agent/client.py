"""Signed control-plane client used by the local science worker CLI."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.services.worker_contract import (
    WORKER_PROTOCOL_VERSION,
    canonical_json,
    canonical_worker_request,
    normalize_release_commit,
    release_commits_compatible,
)
from app.services.registered_workflows import (
    UNION3_REPRODUCTION_WORKFLOW_ID,
    get_registered_dataset_pins,
)


class WorkerClientError(RuntimeError):
    """A local configuration, trust, or control-plane protocol error."""


@dataclass(frozen=True)
class WorkerConfig:
    control_plane_url: str
    worker_id: str
    worker_name: str
    private_key: str
    protocol_version: str
    task_signing_key_id: str
    task_signing_public_key: str


def default_worker_home() -> Path:
    configured = os.getenv("ASTRO_WORKER_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".standard-astro" / "worker"


def config_path(home: Path | None = None) -> Path:
    return (home or default_worker_home()) / "node.json"


def state_path(home: Path | None = None) -> Path:
    return (home or default_worker_home()) / "status.json"


def save_config(config: WorkerConfig, *, home: Path | None = None) -> Path:
    payload = asdict(config)
    payload["control_plane_url"] = _control_plane_base_url(config.control_plane_url)
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path


def load_config(*, home: Path | None = None) -> WorkerConfig:
    path = config_path(home)
    try:
        if path.stat().st_mode & 0o077:
            raise WorkerClientError(f"Worker config permissions must be 0600: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        config = WorkerConfig(**payload)
        normalized_url = _control_plane_base_url(config.control_plane_url)
        return WorkerConfig(
            **{
                **asdict(config),
                "control_plane_url": normalized_url,
            }
        )
    except FileNotFoundError as exc:
        raise WorkerClientError("Worker is not enrolled; run `astro worker enroll`") from exc
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, WorkerClientError):
            raise
        raise WorkerClientError(f"Worker config is invalid: {path}") from exc


def save_status(payload: dict[str, Any], *, home: Path | None = None) -> None:
    path = state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def generate_private_key() -> tuple[Ed25519PrivateKey, str, str]:
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
        private,
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def _private_key(value: str) -> Ed25519PrivateKey:
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise WorkerClientError("Worker private key is invalid") from exc


def _control_plane_base_url(value: str) -> str:
    """Validate and normalize the origin used for Worker enrollment."""

    raw = str(value or "").strip()
    if (
        not raw
        or any(ord(character) <= 32 for character in raw)
        or "\\" in raw
        or "?" in raw
        or "#" in raw
    ):
        raise WorkerClientError(
            "Control plane URL must be an origin without query or fragment"
        )
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise WorkerClientError("Control plane URL is invalid") from exc
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not hostname
        or port == 0
        or parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
    ):
        raise WorkerClientError(
            "Control plane URL must be an HTTPS origin without credentials or path"
        )
    hostname = hostname.lower()
    if scheme == "http":
        try:
            address = ipaddress.ip_address(hostname) if "%" not in hostname else None
        except ValueError:
            address = None
        loopback = bool(
            (isinstance(address, ipaddress.IPv4Address) and address.is_loopback)
            or address == ipaddress.IPv6Address("::1")
        )
        if not loopback:
            raise WorkerClientError(
                "Control plane must use HTTPS; HTTP is limited to an exact loopback host"
            )
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    rendered_port = f":{port}" if port is not None else ""
    return f"{scheme}://{rendered_host}{rendered_port}"


def enroll(
    *,
    control_plane_url: str,
    enrollment_code: str,
    worker_name: str,
    home: Path | None = None,
    timeout: float = 30.0,
) -> WorkerConfig:
    """Generate the node key locally and consume a one-time enrollment code."""

    base_url = _control_plane_base_url(control_plane_url)
    _private, private_text, public_text = generate_private_key()
    response = httpx.post(
        base_url + "/api/compute/v1/nodes/enroll",
        json={
            "enrollment_code": enrollment_code,
            "name": worker_name,
            "public_key": public_text,
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "capabilities": {
                "workflows": ["union3_flat_lcdm_sn_only_v1"],
                "concurrency": 1,
            },
            "release_manifest": {
                "git_commit": os.getenv("GIT_COMMIT") or os.getenv("TOOL_VERSION") or "unknown",
                "image_digest": os.getenv("WORKER_IMAGE_DIGEST") or "unknown",
            },
        },
        timeout=timeout,
        follow_redirects=False,
        trust_env=base_url.startswith("https://"),
    )
    if response.status_code != 201:
        raise WorkerClientError(
            f"Enrollment failed ({response.status_code}): {response.text[:500]}"
        )
    payload = response.json()
    task_key = payload.get("task_signing_key") or {}
    if task_key.get("algorithm") != "ed25519":
        raise WorkerClientError("Control plane did not provide an Ed25519 task trust key")
    config = WorkerConfig(
        control_plane_url=base_url,
        worker_id=str(uuid.UUID(str(payload["node_id"]))),
        worker_name=worker_name,
        private_key=private_text,
        protocol_version=WORKER_PROTOCOL_VERSION,
        task_signing_key_id=str(task_key["key_id"]),
        task_signing_public_key=str(task_key["public_key"]),
    )
    save_config(config, home=home)
    save_status(
        {
            "state": "enrolled",
            "worker_id": config.worker_id,
            "last_error": None,
        },
        home=home,
    )
    return config


def signed_request(
    config: WorkerConfig,
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    query: str = "",
    timeout: float = 35.0,
) -> httpx.Response:
    base_url = _control_plane_base_url(config.control_plane_url)
    body = canonical_json(payload) if payload is not None else b""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    message = canonical_worker_request(
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=hashlib.sha256(body).hexdigest(),
        worker_id=config.worker_id,
        protocol_version=config.protocol_version,
    )
    signature = _private_key(config.private_key).sign(message)
    headers = {
        "x-standard-astro-worker-id": config.worker_id,
        "x-standard-astro-worker-timestamp": timestamp,
        "x-standard-astro-worker-nonce": nonce,
        "x-standard-astro-worker-protocol": config.protocol_version,
        "x-standard-astro-worker-signature": base64.b64encode(signature).decode("ascii"),
    }
    if payload is not None:
        headers["content-type"] = "application/json"
    return httpx.request(
        method,
        base_url + path + query,
        content=body,
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=base_url.startswith("https://"),
    )


def verify_task_envelope(
    config: WorkerConfig,
    envelope: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    signature_record = envelope.get("server_signature")
    if not isinstance(signature_record, dict):
        raise WorkerClientError("Task envelope is unsigned")
    if (
        signature_record.get("algorithm") != "ed25519"
        or signature_record.get("key_id") != config.task_signing_key_id
    ):
        raise WorkerClientError("Task envelope uses an untrusted control key")
    try:
        public_raw = base64.b64decode(config.task_signing_public_key, validate=True)
        signature = base64.b64decode(signature_record["value"], validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerClientError("Task envelope signature encoding is invalid") from exc
    unsigned = dict(envelope)
    unsigned.pop("server_signature", None)
    try:
        public_key.verify(signature, canonical_json(unsigned))
    except InvalidSignature as exc:
        raise WorkerClientError("Task envelope signature is invalid") from exc
    if envelope.get("protocol_version") != config.protocol_version:
        raise WorkerClientError("Task envelope protocol version is unsupported")
    if envelope.get("workflow_key") != "union3_flat_lcdm_sn_only_v1":
        raise WorkerClientError("Task requested an unregistered workflow")
    required_keys = {
        "protocol_version",
        "job_id",
        "audit_id",
        "attempt_id",
        "lease_id",
        "workflow_key",
        "normalized_inputs",
        "input_sha256",
        "dataset_pins",
        "image_digest",
        "git_commit",
        "resource_limits",
        "deadline",
        "lease_expires_at",
        "server_signature",
    }
    if set(envelope) != required_keys:
        raise WorkerClientError("Task envelope shape is not registered")
    try:
        uuid.UUID(str(envelope["attempt_id"]))
        lease_id = str(envelope["lease_id"])
        input_sha256 = str(envelope["input_sha256"])
        lease_expires_at = datetime.fromisoformat(
            str(envelope["lease_expires_at"]).replace("Z", "+00:00")
        )
        deadline = datetime.fromisoformat(
            str(envelope["deadline"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise WorkerClientError("Task envelope bindings are invalid") from exc
    if (
        len(lease_id) != 64
        or any(character not in "0123456789abcdef" for character in lease_id)
        or len(input_sha256) != 64
        or any(character not in "0123456789abcdef" for character in input_sha256)
    ):
        raise WorkerClientError("Task envelope hashes are invalid")
    if lease_expires_at.tzinfo is None or deadline.tzinfo is None:
        raise WorkerClientError("Task envelope times must include a timezone")
    checked_at = now or datetime.now(timezone.utc)
    if lease_expires_at <= checked_at or deadline <= lease_expires_at:
        raise WorkerClientError("Task lease is expired or internally inconsistent")

    expected_pins = get_registered_dataset_pins(UNION3_REPRODUCTION_WORKFLOW_ID)
    if envelope.get("dataset_pins") != expected_pins:
        raise WorkerClientError("Task requested unregistered dataset pins")
    limits = envelope.get("resource_limits")
    if limits != {"cpu": 2, "memory_mb": 6144, "concurrency": 1}:
        raise WorkerClientError("Task requested unregistered resource limits")
    required_digest = str(envelope.get("image_digest") or "")
    local_digest = str(os.getenv("WORKER_IMAGE_DIGEST") or "")
    required_commit = normalize_release_commit(envelope.get("git_commit"))
    local_commit = normalize_release_commit(
        os.getenv("GIT_COMMIT") or os.getenv("TOOL_VERSION")
    )
    if local_digest and not required_digest:
        raise WorkerClientError("Task omitted the pinned signed worker image digest")
    if required_digest and not local_digest:
        raise WorkerClientError("Worker image digest is unavailable locally")
    if local_digest and required_digest and local_digest != required_digest:
        raise WorkerClientError("Task requires a different signed worker image digest")
    if local_digest and not local_commit:
        raise WorkerClientError("Worker release commit is unavailable locally")
    if local_commit and not release_commits_compatible(required_commit, local_commit):
        raise WorkerClientError("Task requires a different worker release commit")
