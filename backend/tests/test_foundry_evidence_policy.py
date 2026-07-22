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
        "evidence_pack_id=n0ne",
        "evidence_pack_id=fals3",
        "evidence_pack_id=正式包",
        "evidencePack=正式包",
        "evidence_pack_id=Δ",
        "evidence_pack_id=📦",
        "evidence_pack_id=。",
        "evidence_pack_id=null,pack-123",
        '{"evidence_pack_id": "pack-123"}',
        "Evidence Pack ID: 123e4567-e89b-12d3-a456-426614174000",
        "Evidence Pack ID is pack-123",
        "Evidence Pack ID is n〇ne",
        "Evidence Pack ID is 正式包",
        "Evidence Pack ID is📦",
        "Evidence Pack ID is 。",
        "Evidence Pack ID is unavailable, pack-123",
        "Evidence Pack ID is intentionally unavailable; actual ID pack-123",
        "Evidence Pack ID is empty. pack-123",
        "Evidence Pack ID is: pack-123",
        "Evidence Pack ID is(pack-123)",
        "Evidence Pack ID is\u200bpack-123",
        "Evidence Pack's ID is pack-123",
        "Evidence Pack identifier is pack-123",
        "Evidence Pack is pack-123",
        "formal_evidence_pack=pack-123",
        "formalEvidencePack=pack-123",
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
        "scientific verdict: SᴜPPORTED",
        "scientific verdict: SᴜΡΡΟRΤΕD",
        "scientific verdict: SUPP0RTED",
        "scientific verdict: 5UPP0R73D",
        "scientific verdict: SUPP٠RTED",
        "scientific verdict: SUPP〇RTED",
        "publicationReady=t\u200brue",
        "publication_ready=١",
        "claim_eligible=۱",
        "evidence_pack_allowed=१",
        "publication_ready=❶",
        "publication ready is true",
        "claim eligible is true",
        "evidence pack allowed is true",
        "evidence class is formal",
        "publicati0n_ready=true",
        "publicati〇n_ready=true",
        "pᴜblication_ready=true",
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
        "Evidence Pack ID is empty.",
        "Evidence Pack ID is unavailable。",
        "Evidence Pack ID is “unavailable”",
        "Evidence Pack ID is unavailable…",
        "Evidence Pack ID issue is tracked.",
        "Evidence Pack is intentionally unavailable.",
        "evidence_pack_id=null",
        "evidence_pack_id=",
        'evidence_pack_id=""',
        "evidence_pack_id=''",
        '{"evidence_pack_id":""}',
        "evidencePack=false",
        "formalEvidencePack=null",
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
        {"scientificVerdict": "SᴜPPORTED"},
        {"scientificVerdict": "SUPP0RTED"},
        {"scientificVerdict": "SUPP٠RTED"},
        {"scientificVerdict": "SUPP〇RTED"},
        {"publicati0n_ready": True},
        {"publicati〇n_ready": True},
        {"pᴜblication_ready": True},
        {"cla1m_eligible": True},
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
        {"message": "The Ωm profile fit completed without a formal verdict."},
        {"天地玄黄宇宙洪荒日月盈昃": True},
    ],
)
def test_nested_policy_allows_absent_ids_and_plain_discussion(
    value: dict[str, object],
) -> None:
    assert contains_formal_claim_escape(value, scan_text_leaves=True) is False


def test_structured_verdict_rejects_confusable_without_text_leaf_scan() -> None:
    for verdict in (
        "SᴜPPORTED",
        "SUPP0RTED",
        "5UPP0R73D",
        "SUPP٠RTED",
        "SUPP〇RTED",
    ):
        assert contains_formal_claim_escape(
            {"scientificVerdict": verdict}
        ) is True


def test_unrelated_cjk_text_is_not_treated_as_supported() -> None:
    assert contains_formal_claim_escape_text("天地玄黄宇宙洪荒日") is False
