#!/usr/bin/env python3
"""Sign a bounded host-generated materialization payload with a dedicated key."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


_DOMAIN = b"standard-astro/foundry-materialization/v1\0"
_PAYLOAD_SCHEMAS = {
    "standard_astro_materialization_pr_v1",
    "standard_astro_materialization_final_v1",
}
_KEY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
# RFC 8410 PKCS#8 prefix for an Ed25519 32-byte private seed.  Keeping the
# conversion here lets the minimal GitHub signing job use the runner's OpenSSL
# instead of assuming the third-party ``cryptography`` package is installed.
_ED25519_PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _load_payload(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 64 * 1024 or path.is_symlink() or not path.is_file():
        raise ValueError("materialization_payload_file_invalid")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("materialization_payload_json_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in _PAYLOAD_SCHEMAS:
        raise ValueError("materialization_payload_schema_invalid")
    # Require the producer to have emitted canonical JSON (with one trailing
    # newline). This prevents alternate encodings from acquiring signatures.
    if raw != canonical(payload) + b"\n":
        raise ValueError("materialization_payload_not_canonical")
    if payload.get("candidate_code_executed") is not False:
        raise ValueError("materialization_candidate_execution_forbidden")
    if payload["schema_version"] == "standard_astro_materialization_pr_v1":
        if payload.get("auto_merge_performed") is not False:
            raise ValueError("materialization_auto_merge_forbidden")
    else:
        if (
            payload.get("source_was_merged") is not True
            or payload.get("validation_image_built_without_execution") is not True
        ):
            raise ValueError("materialization_final_assertions_invalid")
    return payload


def _sign_ed25519(seed: bytes, message: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("materialization_private_key_invalid")
    try:
        with tempfile.NamedTemporaryFile(prefix="foundry-ed25519-key-") as key_file:
            os.fchmod(key_file.fileno(), 0o600)
            key_file.write(_ED25519_PKCS8_PREFIX + seed)
            key_file.flush()
            with tempfile.NamedTemporaryFile(prefix="foundry-signing-input-") as input_file:
                input_file.write(message)
                input_file.flush()
                result = subprocess.run(
                    [
                        "openssl",
                        "pkeyutl",
                        "-sign",
                        "-rawin",
                        "-inkey",
                        key_file.name,
                        "-keyform",
                        "DER",
                        "-in",
                        input_file.name,
                    ],
                    check=False,
                    capture_output=True,
                    timeout=10,
                )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("materialization_signing_backend_unavailable") from exc
    if result.returncode != 0 or len(result.stdout) != 64:
        raise ValueError("materialization_signing_backend_failed")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not _KEY_ID.fullmatch(args.key_id):
        raise ValueError("materialization_key_id_invalid")
    payload = _load_payload(Path(args.payload))
    try:
        seed = base64.b64decode(args.private_key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("materialization_private_key_invalid") from exc
    if len(seed) != 32:
        raise ValueError("materialization_private_key_invalid")
    canonical_payload = canonical(payload)
    signature = _sign_ed25519(seed, _DOMAIN + canonical_payload)
    envelope = {
        "schema_version": "standard_astro_materialization_attestation_bundle_v1",
        "payload": payload,
        "payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
        "signature": {
            "algorithm": "ed25519",
            "key_id": args.key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }
    envelope["receipt_sha256"] = hashlib.sha256(canonical(envelope)).hexdigest()
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        raise ValueError("materialization_output_exists")
    output.write_bytes(canonical(envelope) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
