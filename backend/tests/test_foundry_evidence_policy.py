from __future__ import annotations

import pytest

from app.services.foundry_evidence_policy import (
    contains_formal_claim_escape,
    contains_formal_claim_escape_text,
)


@pytest.mark.parametrize(
    "value",
    [
        "evidence_pack_id=pack-123",
        '{"evidence_pack_id": "pack-123"}',
        "Evidence Pack ID: 123e4567-e89b-12d3-a456-426614174000",
        b"candidate emitted evidence-pack-id='pack/alpha'",
        'evidencePack={"id":"pack-123"}',
        "scientific verdict: SUP\u200bPORTED",
        "scientific verdict: SUP\u034fPORTED",
        "scientific verdict: SUP\ufe0fPORTED",
        "scientific verdict: SUP\u115fPORTED",
        "scientific verdict: SUP\u1160PORTED",
        "scientific verdict: SUP\u3164PORTED",
        "scientific verdict: SUP\uffa0PORTED",
        "scientific verdict: SUP\u2800PORTED",
        "publicationReady=t\u200brue",
        "publication.ready=true",
        "publication/ready=true",
        "evidence.pack.id=pack-1",
        "This dataset is not supported.",
        "It is not false that the result is SUPPORTED.",
        "SUP\x00PORTED",
        "SUP\x08PPORTED",
        "SUP\x1b[31mPORTED\x1b[0m",
        "SUP\x7fPORTED",
        "SUРPORTED",
        b"SUP\x9b31mPORTED",
        b"SUP\x80PORTED",
    ],
)
def test_text_policy_rejects_nonempty_evidence_pack_identifiers(
    value: bytes | str,
) -> None:
    assert contains_formal_claim_escape_text(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "The evidence_pack_id field is reserved for formal runs.",
        "Evidence Pack ID is intentionally unavailable.",
        "evidence_pack_id=null",
        "evidence_pack_id=",
    ],
)
def test_text_policy_does_not_reject_plain_field_discussion(value: str) -> None:
    assert contains_formal_claim_escape_text(value) is False


@pytest.mark.parametrize(
    "value",
    [
        {"Evidence Pack ID": "pack-123"},
        {"message": "evidence_pack_id=pack-123"},
        {"evidence-pack-id": "pack-123"},
        {"publicationReady": True},
        {"claimEligible": True},
        {"evidencePackAllowed": True},
        {"evidenceClass": "FORMAL"},
        {"evidencePack": {"id": "pack-123"}},
        {"scientificVerdict": "SUP\u200bPORTED"},
    ],
)
def test_nested_policy_rejects_evidence_pack_identifier_keys_and_values(
    value: dict[str, object],
) -> None:
    assert contains_formal_claim_escape(value, scan_text_leaves=True) is True


@pytest.mark.parametrize(
    "value",
    [
        {"evidence_pack_id": None},
        {"Evidence Pack ID": ""},
        {"message": "The evidence_pack_id field is reserved."},
    ],
)
def test_nested_policy_allows_absent_ids_and_plain_discussion(
    value: dict[str, object],
) -> None:
    assert contains_formal_claim_escape(value, scan_text_leaves=True) is False
