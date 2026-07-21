#!/usr/bin/env python3
"""Prepare or verify a public, repository-baked Registry activation bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import uuid
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.foundry_registry_activation import (  # noqa: E402
    ACTIVATION_EXPORT_SCHEMA,
    ACTIVATION_MANIFEST_BASENAME,
    ACTIVATION_MANIFEST_SCHEMA,
    ACTIVATION_MODE,
    SIGNED_SNAPSHOT_BASENAME,
    TRUSTED_KEYRING_BASENAME,
    read_packaged_activation,
)
from app.services.workflow_registry_v2 import (  # noqa: E402
    WorkflowRegistryError,
    verify_signed_registry_snapshot,
)


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_EXPORT_FIELDS = {
    "schema_version",
    "release_request_id",
    "release_request_sha256",
    "registry_release_import_id",
    "registry_release_import_receipt_sha256",
    "registry_epoch",
    "registry_snapshot_sha256",
    "signing_key_id",
    "signed_snapshot",
    "trusted_keyring",
    "runtime_registry_modified",
    "activation_required",
}


def _canonical_file_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _read_json_file(path: Path, *, maximum_bytes: int) -> Any:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > maximum_bytes
    ):
        raise ValueError(f"invalid bounded JSON file: {path}")
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )


def _validated_export(path: Path) -> dict[str, Any]:
    envelope = _read_json_file(path, maximum_bytes=12 * 1024 * 1024)
    if (
        not isinstance(envelope, dict)
        or set(envelope) != _EXPORT_FIELDS
        or envelope.get("schema_version") != ACTIVATION_EXPORT_SCHEMA
        or envelope.get("runtime_registry_modified") is not False
        or envelope.get("activation_required")
        != "protected_commit_and_fresh_process"
        or not _SHA256.fullmatch(
            str(envelope.get("release_request_sha256") or "")
        )
        or not _SHA256.fullmatch(
            str(envelope.get("registry_release_import_receipt_sha256") or "")
        )
        or not _SHA256.fullmatch(
            str(envelope.get("registry_snapshot_sha256") or "")
        )
    ):
        raise ValueError("activation export shape or binding is invalid")
    request_id = str(uuid.UUID(str(envelope.get("release_request_id") or "")))
    import_id = str(uuid.UUID(str(envelope.get("registry_release_import_id") or "")))
    if (
        request_id != envelope["release_request_id"]
        or import_id != envelope["registry_release_import_id"]
    ):
        raise ValueError("activation export UUID is not canonical")
    signed = envelope.get("signed_snapshot")
    keyring = envelope.get("trusted_keyring")
    key_id = str(envelope.get("signing_key_id") or "")
    if (
        not isinstance(signed, dict)
        or not isinstance(keyring, dict)
        or set(keyring) != {key_id}
        or not isinstance(keyring.get(key_id), str)
        or not keyring[key_id]
    ):
        raise ValueError("activation export trust material is invalid")
    try:
        payload = verify_signed_registry_snapshot(signed, keyring)
    except WorkflowRegistryError as exc:
        raise ValueError(f"signed Registry verification failed: {exc.code}") from exc
    if (
        signed.get("payload_sha256") != envelope["registry_snapshot_sha256"]
        or payload.get("registry_epoch") != envelope.get("registry_epoch")
        or (signed.get("signature") or {}).get("key_id") != key_id
    ):
        raise ValueError("activation export signed snapshot is not exact")
    return envelope


def _service_bounds(lines: list[str], service_name: str) -> tuple[int, int]:
    marker = f"    name: {service_name}\n"
    try:
        name_index = lines.index(marker)
    except ValueError as exc:
        raise ValueError(f"Render service is missing: {service_name}") from exc
    start = name_index - 1
    while start >= 0 and not lines[start].startswith("  - type:"):
        start -= 1
    if start < 0:
        raise ValueError(f"Render service block is invalid: {service_name}")
    end = start + 1
    while end < len(lines) and not lines[end].startswith("  - type:"):
        end += 1
    return start, end


def update_render_blueprint(path: Path) -> None:
    """Require manual exact-commit deploys before preparing any bundle.

    Public activation files are discovered inside the immutable image; this
    function deliberately does not mutate Render environment configuration.
    """

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for service_name in (
        "standard-astro-backend",
        "standard-astro-celery-worker",
        "standard-astro-celery-beat",
    ):
        start, end = _service_bounds(lines, service_name)
        if "    autoDeployTrigger: off\n" not in lines[start:end]:
            raise ValueError(
                f"Render auto deploy must be off before Registry activation: "
                f"{service_name}"
            )


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    envelope = _validated_export(args.export)
    prepared_commit = str(args.prepared_from_git_commit or "").strip().lower()
    if not _GIT_SHA.fullmatch(prepared_commit):
        raise ValueError("prepared-from Git commit must be exactly 40 lowercase hex")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink():
        raise ValueError("activation output directory cannot be a symlink")

    signed_raw = _canonical_file_bytes(envelope["signed_snapshot"])
    keyring_raw = _canonical_file_bytes(envelope["trusted_keyring"])
    manifest = {
        "schema_version": ACTIVATION_MANIFEST_SCHEMA,
        "release_request_id": envelope["release_request_id"],
        "release_request_sha256": envelope["release_request_sha256"],
        "registry_release_import_id": envelope["registry_release_import_id"],
        "registry_release_import_receipt_sha256": envelope[
            "registry_release_import_receipt_sha256"
        ],
        "registry_epoch": envelope["registry_epoch"],
        "registry_snapshot_sha256": envelope["registry_snapshot_sha256"],
        "signing_key_id": envelope["signing_key_id"],
        "signed_snapshot_path": SIGNED_SNAPSHOT_BASENAME,
        "signed_snapshot_file_sha256": _sha256(signed_raw),
        "trusted_keyring_path": TRUSTED_KEYRING_BASENAME,
        "trusted_keyring_file_sha256": _sha256(keyring_raw),
        "prepared_from_git_commit": prepared_commit,
        "runtime_activation_mode": ACTIVATION_MODE,
    }
    (output_dir / SIGNED_SNAPSHOT_BASENAME).write_bytes(signed_raw)
    (output_dir / TRUSTED_KEYRING_BASENAME).write_bytes(keyring_raw)
    (output_dir / ACTIVATION_MANIFEST_BASENAME).write_bytes(
        _canonical_file_bytes(manifest)
    )
    update_render_blueprint(args.render_blueprint)
    return verify_bundle(
        output_dir,
        expected_request_id=envelope["release_request_id"],
        expected_request_hash=envelope["release_request_sha256"],
    )


def verify_bundle(
    output_dir: Path,
    *,
    expected_request_id: str | None = None,
    expected_request_hash: str | None = None,
) -> dict[str, Any]:
    packaged = read_packaged_activation(output_dir / ACTIVATION_MANIFEST_BASENAME)
    manifest = packaged.manifest
    if expected_request_id is not None and manifest["release_request_id"] != str(
        uuid.UUID(expected_request_id)
    ):
        raise ValueError("activation request id mismatch")
    if expected_request_hash is not None and (
        not _SHA256.fullmatch(expected_request_hash)
        or manifest["release_request_sha256"] != expected_request_hash
    ):
        raise ValueError("activation request hash mismatch")
    return {
        "release_request_id": manifest["release_request_id"],
        "release_request_sha256": manifest["release_request_sha256"],
        "registry_epoch": manifest["registry_epoch"],
        "registry_snapshot_sha256": manifest["registry_snapshot_sha256"],
        "activation_manifest_sha256": packaged.manifest_hash,
        "signing_key_id": manifest["signing_key_id"],
    }


def _write_github_output(path: Path, values: dict[str, Any]) -> None:
    allowed = {
        "release_request_id",
        "release_request_sha256",
        "registry_epoch",
        "registry_snapshot_sha256",
        "activation_manifest_sha256",
        "signing_key_id",
    }
    with path.open("a", encoding="utf-8") as handle:
        for key in sorted(allowed):
            value = str(values[key])
            if "\n" in value or "\r" in value:
                raise ValueError("GitHub output contains a newline")
            handle.write(f"{key}={value}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--export", type=Path, required=True)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    prepare_parser.add_argument("--prepared-from-git-commit", required=True)
    prepare_parser.add_argument("--render-blueprint", type=Path, required=True)
    prepare_parser.add_argument("--github-output", type=Path)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser.add_argument("--release-request-id")
    verify_parser.add_argument("--release-request-sha256")
    verify_parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "prepare":
        result = prepare(args)
    else:
        result = verify_bundle(
            args.output_dir,
            expected_request_id=args.release_request_id,
            expected_request_hash=args.release_request_sha256,
        )
    if args.github_output:
        _write_github_output(args.github_output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
