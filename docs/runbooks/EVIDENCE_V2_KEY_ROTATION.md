# Evidence Pack v2 Signing Key: Generation and Rotation

The Evidence Pack v2 trust chain is anchored in two places that must agree:

1. `keys/evidence-keyring.json` — the committed, out-of-band public keyring
   (what an offline verifier trusts).
2. `EVIDENCE_V2_*` environment variables — what the running service signs
   with and serves at `/.well-known/standard-astro-evidence-keys.json`.

The committed keyring is authoritative for third parties: anyone verifying a
pack offline (`scripts/verify_evidence_pack.py`) trusts the repo file, not
the server. Never let the two drift silently.

## Generate the first key

```bash
cd backend
./venv/bin/python scripts/ops/generate_evidence_v2_keypair.py --key-id evidence-2026-07
```

The script prints to stdout only and never writes files.

1. Put `EVIDENCE_V2_SIGNING_PRIVATE_KEY` and `EVIDENCE_V2_SIGNING_KEY_ID`
   into the secret store (local `.env`, Render dashboard). The private seed
   must never be committed, logged, or pasted into chat transcripts.
2. Set `EVIDENCE_V2_SIGNING_PUBLIC_KEY` to the printed public key.
3. Append the printed keyring record to `keys/evidence-keyring.json`
   (`keys` array) and commit. The commit message should name the key id and
   fingerprint.
4. Mirror the record into `EVIDENCE_V2_VERIFICATION_KEYS` (JSON object keyed
   by key id) so the served keyring matches the committed one.
5. Publish the fingerprint in README's citation/verification section.

## Rotate a key

1. Generate a new keypair with a new `--key-id` (date-stamped).
2. In `keys/evidence-keyring.json`: keep the old record but change its
   `status` to `"retired"` and add `not_before`/`not_after` covering its
   real service window (the parser requires both timestamps on retired
   keys). Append the new record with `status: "active"`.
3. Update the four `EVIDENCE_V2_*` variables to the new key; keep the old
   public record inside `EVIDENCE_V2_VERIFICATION_KEYS` so old packs still
   verify inside their validity window.
4. Commit the keyring change before signing anything with the new key —
   a pack signed by a key that is not yet in the committed keyring cannot
   be verified offline.

## Revoke a compromised key

1. Set the record's `status` to `"revoked"` in both the committed keyring
   and `EVIDENCE_V2_VERIFICATION_KEYS`. Verification of every pack signed by
   that key fails from that point on, by design.
2. Rotate to a fresh key as above.
3. Note the revocation and its reason in `docs/HONESTY_EVIDENCE.md` — hiding
   a revocation would defeat the purpose of the trust chain.

## Consistency check (run after any change)

```bash
cd backend
./venv/bin/pytest tests/test_offline_verifier_parity.py tests/test_evidence_pack_v2.py -q --no-cov
# and manually: served keyring (if the deployment is up) matches the repo file
curl -s <base-url>/.well-known/standard-astro-evidence-keys.json | diff - ../keys/evidence-keyring.json
```
