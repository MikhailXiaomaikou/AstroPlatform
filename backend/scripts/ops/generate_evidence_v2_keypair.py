#!/usr/bin/env python3
"""Generate an Evidence Pack v2 Ed25519 signing keypair.

Prints everything to stdout and NEVER writes a file: the private seed goes
into the operator's secret store (EVIDENCE_V2_SIGNING_PRIVATE_KEY), the
public half goes into keys/evidence-keyring.json and
EVIDENCE_V2_VERIFICATION_KEYS. Run from backend/:

    ./venv/bin/python scripts/ops/generate_evidence_v2_keypair.py --key-id evidence-2026-07

See docs/runbooks/EVIDENCE_V2_KEY_ROTATION.md for the full procedure.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key-id",
        required=True,
        help="Stable identifier for this key, e.g. evidence-2026-07",
    )
    args = parser.parse_args()

    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    seed_b64 = base64.b64encode(seed).decode("ascii")
    public_b64 = base64.b64encode(public).decode("ascii")
    fingerprint = "sha256:" + hashlib.sha256(public).hexdigest()

    keyring_record = {
        "key_id": args.key_id,
        "algorithm": "ed25519",
        "public_key": public_b64,
        "fingerprint": fingerprint,
        "status": "active",
    }

    print("# PRIVATE — secret store only; never commit, never log:")
    print(f"EVIDENCE_V2_SIGNING_PRIVATE_KEY={seed_b64}")
    print(f"EVIDENCE_V2_SIGNING_KEY_ID={args.key_id}")
    print()
    print("# PUBLIC — safe to publish:")
    print(f"EVIDENCE_V2_SIGNING_PUBLIC_KEY={public_b64}")
    print(f"fingerprint: {fingerprint}")
    print()
    print("# Keyring record for keys/evidence-keyring.json and")
    print("# EVIDENCE_V2_VERIFICATION_KEYS:")
    print(json.dumps(keyring_record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
