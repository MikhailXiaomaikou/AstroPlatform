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
        "evidence_class=FORMAL_REGISTRY",
        "evidence_class=formal_evidence",
        "evidence_class=registered_result",
        "evidence_class=publication_ready_candidate",
        "evidence_class is_formal_evidence",
        "evidence_class=FORMALREGISTRY",
        "evidence_class=formalEvidence",
        "evidence_class=F0RMAL_EVIDENCE",
        "evidence_class=REG1STERED_EVIDENCE",
        "evidence_class=A_READY",
        "evidence_class=model_adequacy",
        "evidence_class=",
        '{"evidence_class":"FORMAL_REGISTRY"}',
        'evidence_class="NON_FORMAL_DEMO"FORMAL_REGISTRY',
        'evidence_class="NON_FORMAL_DEMO"_FORMAL',
        '{"evidence_class":"NON_FORMAL_DEMO" FORMAL_REGISTRY}',
        "evidence_class=NON_FORMAL_DEMO FORMAL_REGISTRY",
        'evidence_class="NON_FORMAL_DEMO":FORMAL_REGISTRY',
        'evidence_class="NON_FORMAL_DEMO",FORMAL_REGISTRY',
        'evidence_class="NON_FORMAL_DEMO";FORMAL_REGISTRY',
        'evidence_class="NON_FORMAL_DEMO".FORMAL_REGISTRY',
        'evidence_class="NON_FORMAL_DEMO")FORMAL_REGISTRY',
        "—evidence_class=FORMAL",
        "–evidence_class=FORMAL",
        "•evidence_class=FORMAL",
        "§evidence_class=FORMAL",
        "§publication_ready=true",
        "•evidence_pack_id=pack-1",
        "evidence_pack_id=—",
        "evidence_class - FORMAL_REGISTRY",
        "evidence_class – FORMAL_REGISTRY",
        "evidence_class — FORMAL_REGISTRY",
        "evidence_class − FORMAL_REGISTRY",
        "evidence_class -> FORMAL_REGISTRY",
        "evidence_class=>FORMAL_REGISTRY",
        "evidence_class → FORMAL_REGISTRY",
        "evidence_class ⇒ FORMAL_REGISTRY",
        "evidence_class ⟶ FORMAL_REGISTRY",
        "evidence_class | FORMAL_REGISTRY",
        "Evidence Pack ID — pack-123",
        "Evidence Pack ID -> pack-123",
        "Evidence Pack ID → pack-123",
        "Evidence Pack ID | pack-123",
        "publication_ready — true",
        "claim_eligible -> yes",
        "evidence_pack_allowed | 1",
        "status → SUPPORTED_FINAL",
        "| evidence_class | FORMAL_REGISTRY |",
        "| Evidence Pack ID | pack-123 |",
        "evidence_class │ FORMAL_REGISTRY",
        "evidence_class ┃ FORMAL_REGISTRY",
        "evidence_class ❘ FORMAL_REGISTRY",
        "Evidence Pack ID │ pack-123",
        "|evidence_class|FORMAL_REGISTRY|",
        "|Evidence Pack ID|pack-123|",
        "|publication_ready|on|",
        "publication_ready=on",
        "claim_eligible=enabled",
        "evidence_pack_allowed=on",
        "publication_ready=off",
        "claim_eligible=no",
        "evidence_pack_allowed=0",
        "publication_ready equals true",
        "evidence_class equals FORMAL_REGISTRY",
        "Evidence Pack ID equals pack-123",
        "Evidence Pack's identifier is pack-123",
        "publıcation_ready=true",
        "evıdence_class=FORMAL_REGISTRY",
        "evidence_pack_ıd=pack-123",
        "publication_ready∶true",
        "evidence_class∶FORMAL_REGISTRY",
        "Evidence Pack ID∶pack-123",
        "scientific_verdict=SUPPORTEԁ",
        "pubӏication_ready=true",
        "eviԁence_class=FORMAL_REGISTRY",
        "evidence_pack_ӏd=pack-123",
        "publication_ready (true)",
        "evidence_class (FORMAL_REGISTRY)",
        "Evidence Pack ID (pack-123)",
        "publication_ready¦true",
        "evidence_class∣FORMAL_REGISTRY",
        "Evidence Pack ID≡pack-123",
        "publication_ready was true",
        "evidence_class set to FORMAL_REGISTRY",
        "Evidence Pack ID assigned pack-123",
        "publication_ready equal-to true",
        "evidence_class equal_to FORMAL_REGISTRY",
        "Evidence Pack ID is-equal-to pack-123",
        "scientific_verdict=SՍPPOᖇTEԁ",
        "pubӏιcatiօn_ready=true",
        "publication_ready [true]",
        "evidence_class [FORMAL_REGISTRY]",
        "Evidence Pack ID [pack-123]",
        "publication_ready true",
        "publication_ready—true",
        "publication_ready/true",
        "publication_readyǀtrue",
        "evidence_class⎮FORMAL_REGISTRY",
        "Evidence Pack ID≝pack-123",
        "publication_ready set equal to true",
        "evidence_class set_equal_to FORMAL_REGISTRY",
        "Evidence Pack ID set-equal-to pack-123",
        "publication_ready != false",
        "evidence_class != NON_FORMAL_DEMO",
        "Evidence Pack ID != unavailable",
        "publication_ready ≠ false",
        "evidence_class ≠ NON_FORMAL_DEMO",
        "Evidence Pack ID ≠ unavailable",
        "publication_ready <> false",
        "evidence_class <> NON_FORMAL_DEMO",
        "Evidence Pack ID <> unavailable",
        "publication_ready ¬ false",
        "evidence_class ~ NON_FORMAL_DEMO",
        "scientific_verdict: S U P P O R T E D",
        "scientific_verdict: S-U-P-P-O-R-T-E-D",
        "scientific_verdict: S·U·P·P·O·R·T·E·D",
        "status: S|U|P|P|O|R|T|E|D",
        "scientific verdict: SUP POR TED",
        "publication_ready status=true",
        "claim_eligible status yes",
        "evidence_pack_allowed scientific_verdict=1",
        "evidence_class status=FORMAL_REGISTRY",
        "Evidence Pack ID status=pack-123",
        "scientific_verdict=SUPPORTÉD",
        "scientific_verdict=SÜPPORTED",
        "publicatión_ready=true",
        "evidénce_class=FORMAL_REGISTRY",
        "evidence_pack_íd=pack-1",
        "true = publication_ready",
        "true -> publication_ready",
        "true equal to publication_ready",
        "true equal-to publication_ready",
        "true is equal to publication_ready",
        "not false = publication_ready",
        "!false = publication_ready",
        "false != publication_ready",
        "false ≠ publication_ready",
        "FORMAL_REGISTRY is evidence_class",
        "FORMAL_REGISTRY -> evidence_class",
        "FORMAL_REGISTRY equal to evidence_class",
        "not NON_FORMAL_DEMO is evidence_class",
        "NON_FORMAL_DEMO != evidence_class",
        "pack-123 = Evidence Pack ID",
        "pack-123 -> Evidence Pack ID",
        "pack-123 assigned to Evidence Pack ID",
        "📦 = Evidence Pack ID",
        "正式包 = Evidence Pack ID",
        "not unavailable = Evidence Pack ID",
        "unavailable != Evidence Pack ID",
        "pack-123 is formalEvidencePackId",
        "yes claim_eligible",
        "clairn_eligible=true",
        "evidence_pack_allovved=true",
        "forrnal_evidence_pack_id=pack-1",
        "formalEvidencePackUUID=pack-1",
        "evidencePackUuid=pack-1",
        "evidence_class•FORMAL_REGISTRY",
        "publication_ready•true",
        "pub|ication_ready=true",
        "publiϹation_ready=true",
        "publiϲation_ready=true",
        "pub∣ication_ready=true",
        "pubǀication_ready=true",
        "pu𞣋lication_ready=true",
        "ſormal_evidence_pack_id=pack-1",
        "Evidence Pack ID•pack-123",
        "formalEvidencePackId=pack-123",
        "formal_evidence_pack_id=pack-123",
        "formalEvidencePackIdentifier=pack-123",
        '{"pubӏication_ready":true}',
        '{"pub|ication_ready":true}',
        '{"publiϹation_ready":true}',
        '{"publiϲation_ready":true}',
        '{"pub∣ication_ready":true}',
        '{"pubǀication_ready":true}',
        '{"pu𞣋lication_ready":true}',
        '{"ſormal_evidence_pack_id":"pack-1"}',
        '{"eviԁence_class":"FORMAL_REGISTRY"}',
        '{"evidence_pack_ӏd":"pack-123"}',
        '{"scientific_verdict":"SՍPPOᖇTEԁ"}',
        '{"pubӏιcatiօn_ready":true}',
        '{"formalEvidencePackId":"pack-123"}',
        '{"scientific_verdict":"S U P P O R T E D"}',
        '{"status":"S-U-P-P-O-R-T-E-D"}',
        '{"publicatión_ready":true}',
        '{"evidénce_class":"FORMAL_REGISTRY"}',
        '{"evidence_pack_íd":"pack-1"}',
        '{"clairn_eligible":true}',
        '{"evidence_pack_allovved":true}',
        '{"forrnal_evidence_pack_id":"pack-1"}',
        '{"formalEvidencePackUUID":"pack-1"}',
        '{"evidence_pack_id":0}',
        '{"evidence_pack_id":[]}',
        '{"evidence_pack_id":{}}',
        '{"evidence_class":"NON_FORMAL_DEMO",'
        '"evidence_class":"FORMAL_REGISTRY"}',
        "scientific_verdict=SUPPORTED_FINAL",
        "status=SUPPORTED_RESULT",
        "publication_ready=true_candidate",
        "claim_eligible=yes_result",
        "evidence_pack_allowed=1_result",
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
        "publication_ready\ntrue",
        "publication_ready\nis true",
        "evidence_class\nFORMAL_REGISTRY",
        "Evidence Pack ID\npack-123",
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
        "evidence_class=NON_FORMAL_DEMO",
        "evidence class is NON_FORMAL_DEMO.",
        '{"evidence_class":"NON_FORMAL_DEMO"}',
        '{"evidence_class":"NON_FORMAL_DEMO","note":"candidate"}',
        '{\n  "evidence_class": "NON_FORMAL_DEMO",\n'
        '  "note": "candidate"\n}',
        "not_evidence_class=FORMAL",
        "previous_evidence_class=FORMAL",
        "not_publication_ready=true",
        "candidate_claim_eligible=yes",
        "no_evidence_pack_id=pack-1",
        "前evidence_class=FORMAL",
        "evidence_class — NON_FORMAL_DEMO",
        "evidence_class→NON_FORMAL_DEMO",
        "Evidence Pack ID — unavailable",
        "Evidence Pack ID - intentionally unavailable",
        "| evidence_class | NON_FORMAL_DEMO |",
        "| Evidence Pack ID | unavailable |",
        "|evidence_class|NON_FORMAL_DEMO|",
        "|Evidence Pack ID|unavailable|",
        "|publication_ready|false|",
        "publication_ready equals false",
        "evidence_class equals NON_FORMAL_DEMO",
        "Evidence Pack ID equals unavailable",
        "publication_ready∶false",
        "evidence_class∶NON_FORMAL_DEMO",
        "Evidence Pack ID∶unavailable",
        "publication_ready (false)",
        "evidence_class (NON_FORMAL_DEMO)",
        "Evidence Pack ID (unavailable)",
        "publication_ready¦false",
        "evidence_class∣NON_FORMAL_DEMO",
        "Evidence Pack ID≡unavailable",
        "publication_ready was false",
        "evidence_class set to NON_FORMAL_DEMO",
        "Evidence Pack ID assigned unavailable",
        "publication_ready equal-to false",
        "publication_ready equal_to false",
        "evidence_class is-equal-to NON_FORMAL_DEMO",
        "evidence_class is_equal_to NON_FORMAL_DEMO",
        "Evidence Pack ID is equal-to unavailable",
        "publication_ready is set to false",
        "publication_ready was equal to false",
        "evidence_class is set to NON_FORMAL_DEMO",
        "Evidence Pack ID was set to unavailable",
        "publication_ready ((false))",
        "evidence_class ((NON_FORMAL_DEMO))",
        "Evidence Pack ID ((unavailable))",
        "publication_ready := false",
        "publication_ready == false",
        "publication_ready [false]",
        "evidence_class [NON_FORMAL_DEMO]",
        "Evidence Pack ID [unavailable]",
        "false = publication_ready",
        "NON_FORMAL_DEMO is evidence_class",
        "unavailable = Evidence Pack ID",
        "Evidence Pack's ID is unavailable",
        "Evidence Pack's identifier is unavailable",
        "This candidate is not publication ready.",
        "This result is not claim eligible.",
        "The evidence class is not formal.",
        "This candidate does not create an Evidence Pack.",
        "No Evidence Pack is generated for this non-formal demo.",
        '{"evidence_pack_id":false}',
        "publication_ready — false",
        "status — WITHHELD",
        "evidence_class-like documentation",
        "Evidence Pack ID-like fields",
        "evidence_class‐like documentation",
        "evidence_class‑like documentation",
        "evidence_class–like documentation",
        "evidence_class—like documentation",
        "evidence_class−like documentation",
        "Evidence Pack ID–like fields",
        "evidence_class·like documentation",
        "evidence_class │ NON_FORMAL_DEMO",
        "Evidence Pack ID ┃ unavailable",
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
        {"—evidence_class": "FORMAL"},
        {"•publication_ready": True},
        {"§evidence_pack_id": "pack-1"},
        {"evidence_pack_id": 0},
        {"evidence_pack_id": []},
        {"evidence_pack_id": {}},
        {"evidencePack": []},
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
        {"前evidence_class": "FORMAL"},
        {"前_evidence_class": "FORMAL"},
        {"evidence_pack_id": False},
        {"evidencePack": None},
        {"formalEvidencePack": ""},
        {"formalEvidencePackId": None},
        {"formalEvidencePackIdentifier": ""},
        {"Evidence Pack's ID": None},
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
    assert contains_formal_claim_escape_text(
        "The kernel diagnostic supportΩd remained finite."
    ) is False


@pytest.mark.parametrize(
    "lookalike",
    ["\u02e1", "\u23fd", "\u24db", "\U00011de1"],
)
def test_pinned_uts_sources_cannot_create_safe_pack_placeholders(
    lookalike: str,
) -> None:
    assert contains_formal_claim_escape_text(
        f"evidence_pack_id=nu{lookalike}l"
    ) is True
    assert contains_formal_claim_escape_text(
        f"Evidence Pack ID is nu{lookalike}l"
    ) is True


def test_deep_json_and_cyclic_python_values_fail_closed_without_recursion() -> None:
    deeply_nested = "[" * 500 + "0" + "]" * 500
    assert contains_formal_claim_escape_text(deeply_nested) is False

    cyclic: list[object] = []
    cyclic.append(cyclic)
    assert contains_formal_claim_escape(cyclic, scan_text_leaves=True) is True


@pytest.mark.parametrize(
    "value",
    [
        "pu৪lication_ready=true",
        "publ∣cation_ready=true",
        "claǀm_eligible=true",
        "ev⎮dence_class=FORMAL_REGISTRY",
        "publicati٥n_ready=true",
        "publi𑣩ation_ready=true",
        "clai𑣣_eligible=true",
        "scientific_verdict=SUPP٥RTED",
        "scientific_verdict=SUPP۵RTED",
        "FORMAL_REGISTRY\nevidence_class",
        "pack-123\nEvidence Pack ID",
        "true | publication_ready",
        "FORMAL_REGISTRY | evidence_class",
        "pack-123 | Evidence Pack ID",
        "true becomes publication_ready",
        "on publication_ready",
        "enabled claim_eligible",
        "FORMAL_REGISTRY evidence_class",
        "pack-123 Evidence Pack ID",
        "formalEvidencePackRef=pack-123",
        "formalEvidencePackReference=pack-123",
        "evidencePackRef=pack-123",
        "formal_evidence_pack_ref=pack-123",
        "evidence_pack_id=nοne",
        "evidence_pack_id=nоne",
        "evidence_pack_id=nuⅼl",
        "evidence_pack_id=fαlse",
        "Evidence Pack ID is unavailablе",
        "publication_\nready=true",
        "claim_\neligible=true",
        "evidence_\nclass=FORMAL_REGISTRY",
        "Evidence\nPack ID=pack-1",
        "true: publication_ready=false",
        "yes: claim_eligible=false",
        "FORMAL_REGISTRY: evidence_class=NON_FORMAL_DEMO",
        "pack-123: Evidence Pack ID unavailable",
        "📦: Evidence Pack ID unavailable",
        "Evidence Pack ID=\npack-123",
        "Evidence Pack ID:\npack-123",
        "true =\npublication_ready",
        "true \n= publication_ready",
        "true is\npublication_ready",
        "FORMAL_REGISTRY =\nevidence_class",
        "pack-123 =\nEvidence Pack ID",
        "y\nes claim_eligible",
    ],
)
def test_text_policy_rejects_dual_shadow_and_reverse_layouts(value: str) -> None:
    assert contains_formal_claim_escape_text(value) is True


@pytest.mark.parametrize(
    "value",
    [
        {"pu৪lication_ready": True},
        {"publ∣cation_ready": True},
        {"claǀm_eligible": True},
        {"ev⎮dence_class": "FORMAL_REGISTRY"},
        {"publicati٥n_ready": True},
        {"publi𑣩ation_ready": True},
        {"clai𑣣_eligible": True},
        {"scientific_verdict": "SUPP٥RTED"},
        {"scientific_verdict": "SUPP۵RTED"},
        {"formalEvidencePackRef": "pack-123"},
        {"formalEvidencePackReference": "pack-123"},
        {"evidencePackRef": "pack-123"},
    ],
)
def test_structured_policy_rejects_dual_shadow_keys_and_values(
    value: dict[str, object],
) -> None:
    assert contains_formal_claim_escape(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "This run is not publication ready.",
        "No formal Evidence Pack was generated.",
        "Evidence Pack generation is disabled.",
        "The candidate cannot create an Evidence Pack.",
        "The checksum comparison returned true\npublication_ready=false",
        "Evidence Pack ID=\nunavailable",
        "publication_ready=\nfalse",
        "evidence_class=\nNON_FORMAL_DEMO",
    ],
)
def test_text_policy_allows_closed_negative_prose_and_separate_lines(
    value: str,
) -> None:
    assert contains_formal_claim_escape_text(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "publication\nready\n=true",
        "publication\nready\n=\ntrue",
        "publication\nready\nis equal to\ntrue",
        "publication\nready\n| true",
        "publication\nready\n|\ntrue",
        "claim\neligible\n=yes",
        "evidence\npack\nallowed\n=1",
        "evidence\nclass\n=FORMAL_REGISTRY",
        "evidence\nclass\n| FORMAL_REGISTRY",
        "evidence\nclass\nFORMAL REGISTRY",
        "Evidence\nPack\nID\n=pack-1",
        "Evidence\nPack\nID\n| pack-123",
        "Evidence\nPack\nID\nartifact abc",
        "publication\nready\n(true)",
        "claim\neligible\n[yes]",
        "true\n=\npublication\nready",
        "yes\nis\nclaim\neligible",
        "FORMAL_REGISTRY\n=\nevidence\nclass",
        "pack-123\n|\nEvidence\nPack\nID",
        "(true)\npublication\nready",
        "FORMAL REGISTRY\nevidence\nclass",
        "artifact abc\nEvidence\nPack\nID",
        "INFO: true =\npublication\nready",
        "true is\nequal\nto publication_ready",
        "FORMAL_REGISTRY\nassigned\nto evidence_class",
        "pack-123 was\nset\nto Evidence Pack ID",
        "t\nrue\npublication_ready",
        "y\nes\nclaim_eligible",
        "F O R M A L _ R E G I S T R Y\nevidence_class",
        "p a c k - 1 2 3\nEvidence Pack ID",
        "not false\npublication\nready",
        "REGISTERED_RESULT\nevidence\nclass",
        "REGISTERED_EVIDENCE\nevidence\nclass",
        "is_formal_evidence\nevidence\nclass\n"
        "INFO: unrelated diagnostic passed",
        "publication_ready_candidate\nevidence\nclass",
        "A_READY\nevidence\nclass",
        "(true)\npublication\nready\nINFO: unrelated diagnostic passed",
        "FORMAL REGISTRY\nevidence\nclass\n"
        "INFO: unrelated diagnostic passed",
        "artifact abc\nEvidence\nPack\nID\n"
        "INFO: unrelated diagnostic passed",
        "This candidate is not publication\nready\n=true",
        "This candidate is not publication\nready\n| true",
        "This candidate is not publication ready=true",
        "This result is not claim eligible=yes",
        "The evidence class is not formal=FORMAL_REGISTRY",
        "No formal Evidence Pack was generated=pack-123",
        "This candidate is not publication ready | true",
        "This candidate is not publication ready.=true",
        "This result is not claim eligible. = yes",
        "The evidence class is not formal. = FORMAL_REGISTRY",
        "No formal Evidence Pack was generated. = pack-123",
        "Evidence Pack generation is disabled. = pack-123",
        "This result is not claim\neligible\n=true",
        "The evidence\nclass is not formal\n=FORMAL_REGISTRY",
        "This candidate does not create an Evidence\nPack\n=pack-123",
    ],
)
def test_text_policy_rejects_wrapped_label_with_following_formal_value(
    value: str,
) -> None:
    assert contains_formal_claim_escape_text(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "publication\nready\n=false",
        "publication\nready\n=\nfalse",
        "publication\nready\nis equal to\nfalse",
        "publication\nready\n| false",
        "publication\nready\n|\nfalse",
        "publication_ready\n=\nfalse",
        "false\n=\npublication_ready",
        "claim\neligible\n=false",
        "claim_eligible\nis equal to\nfalse",
        "false\nis equal to\nclaim_eligible",
        "evidence\npack\nallowed\n=false",
        "evidence\nclass\n=NON_FORMAL_DEMO",
        "evidence\nclass\n| NON_FORMAL_DEMO",
        "evidence_class\n=\nNON_FORMAL_DEMO",
        "NON_FORMAL_DEMO\n=\nevidence_class",
        "Evidence\nPack\nID\n=unavailable",
        "Evidence\nPack\nID\n| unavailable",
        "Evidence Pack ID\n=\nunavailable",
        "unavailable\n=\nEvidence Pack ID",
        "publication\nready\nINFO: unrelated diagnostic passed",
        "Evidence\nPack\nID\nissue is tracked.",
        "This candidate is not publication\nready.",
        "This result is not claim\neligible.",
        "The evidence\nclass is not formal.",
        "This candidate does not create an Evidence\nPack.",
        "No Evidence\nPack is generated for this non-formal demo.",
        "This candidate\nis not publication ready.",
        "The evidence class\nis not formal.",
        "The checksum comparison returned true\npublication\nready\n"
        "INFO: unrelated diagnostic passed",
        "ordinary diagnostic complete\nevidence\nclass\n"
        "INFO: unrelated diagnostic passed",
        "step 41 passed\nEvidence\nPack\nID\nissue is tracked.",
        "INFO: false =\npublication\nready",
        "publication_ready=f\nalse",
        "evidence_class=NON_\nFORMAL_DEMO",
        "fa\nlse = publication_ready",
        "false is\nequal\nto publication_ready",
        "NON_FORMAL_DEMO\nassigned\nto evidence_class",
        "unavailable was\nset\nto Evidence Pack ID",
        "fa\nlse\npublication_ready",
        "INFO: pre\nThis candidate\nis not publication ready.\nINFO: post",
        "INFO: pre\nNo formal\nEvidence Pack was generated.\nINFO: post",
        "The model diagnostic passed\nevidence\nclass\n"
        "INFO: unrelated diagnostic passed",
        "evidence checksum verified\nevidence\nclass\n"
        "INFO: unrelated diagnostic passed",
        "registry cache refreshed\nevidence\nclass\n"
        "INFO: unrelated diagnostic passed",
    ],
)
def test_text_policy_allows_wrapped_label_with_closed_non_formal_value(
    value: str,
) -> None:
    assert contains_formal_claim_escape_text(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "true\npublication_ready=false",
        "FORMAL_REGISTRY\nevidence_class=NON_FORMAL_DEMO",
        "pack-123\nEvidence Pack ID unavailable",
        "publication_ready=false\n=true",
        "publication_ready=false\nbecomes true",
        "publication_ready=false\nt\nrue",
        "publication_ready=false\nbe\ncomes true",
        "publication_ready\n=\nfalse\n=true",
        "evidence_class\n=\nNON_FORMAL_DEMO\n=FORMAL_REGISTRY",
        "Evidence Pack ID\n=\nunavailable\n=pack-123",
        "true=\nfalse=\npublication_ready",
        "FORMAL_REGISTRY=\nNON_FORMAL_DEMO=\nevidence_class",
        "pack-123=\nunavailable=\nEvidence Pack ID",
        "true =\nINFO: publication_ready=false",
        "publication\nready\nINFO: =true",
        "evidence\nclass\nINFO: FORMAL_REGISTRY",
        "Evidence\nPack\nID\nINFO: pack-123",
        "INFO: t\nINFO: rue\npublication_ready=false",
        "publication_ready=false\nINFO: t\nINFO: rue",
        "publication_ready=false\nINFO: be\nINFO: comes true",
        "INFO: FORMAL_\nINFO: REGISTRY\n"
        "evidence_class=NON_FORMAL_DEMO",
        "REG1STERED_EVIDENCE\nevidence\nclass",
        "F0RMAL_EVIDENCE\nevidence\nclass",
        "FORMAL_REGISTRY_V2\nevidence_class",
        "MODEL_ADEQUACY_V2\nevidence_class",
        "This candidate is not publication ready (true)",
        "This result is not claim eligible [yes]",
        "The evidence class is not formal (FORMAL_REGISTRY)",
        "No formal Evidence Pack was generated (pack-123)",
        "This candidate is not publication ready."
        + (" " * 1_024)
        + "=true",
        "No formal Evidence Pack was generated."
        + (" " * 1_024)
        + "=pack-123",
        "publication_ready\nINFO: INFO: true",
        "publication_ready=false\nINFO: INFO: true",
        "true =\nINFO: INFO: publication_ready=false",
        "This candidate is not publication ready.\nINFO: true",
        "This candidate is not publication ready.\nt\nrue",
        "true=\nThis candidate is not publication ready.",
        "FORMAL_REGISTRY=\nThe evidence class is not formal.",
        "pack-123=\nNo formal Evidence Pack was generated.",
        "true=\npublication_ready\nfield is reserved",
        "pack-123=\nEvidence Pack ID\nissue is tracked.",
        "true=\nResult: publication_ready=false",
        "true=\n- publication_ready=false",
        "true=\n[publication_ready=false]",
        "publication_ready=false\n-\n>\ntrue",
        "publication_ready=false\n!\n=\nfalse",
        "publication_ready=false\n<\nfalse",
        "Evidence Pack ID=unavailable\nabc\ndef\n1",
        "publication_\nINFO: ready=true",
        "true=publication_\nINFO: ready",
        "publication_ready\nINFO:\ntrue",
        "Evidence Pack ID\nINFO:\npack-123",
        "true =\nINFO:\npublication_ready=false",
        "publication_ready\nIN\nFO: true",
        "publication\nready\nIN\nFO: =true",
        "evidence_class\nIN\nFO: FORMAL_REGISTRY",
        "true =\nIN\nFO: publication_ready=false",
        "INFO: p\nINFO: ub\nINFO: lication_ready=true",
        "INFO: Evidence\nINFO:  \nINFO: Pack ID=pack-123",
        "publication_ready=\nINFO: INF\nO: true",
        "evidence_class=\nINFO: INF\nO: FORMAL_REGISTRY",
        "Evidence Pack ID=p\nINFO: INF\nO: ack-123",
        "publication_ready\nINFO\n:true",
        "INFO:\rINF\nO:publication_ready=true",
        "publication_ready=\rINFO\n: true",
        "publication_ready\nIN\n\n\nFO:true",
    ],
)
def test_text_policy_rejects_every_value_in_assignment_chains(value: str) -> None:
    assert contains_formal_claim_escape_text(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "false\npublication_ready=false",
        "NON_FORMAL_DEMO\nevidence_class=NON_FORMAL_DEMO",
        "unavailable\nEvidence Pack ID unavailable",
        "publication\nready\nINFO: false",
        "false =\nINFO: publication_ready=false",
        "No formal Evidence Pack was generated.\nordinary diagnostic complete",
        "Evidence Pack generation is disabled.\nordinary diagnostic complete",
        "INFO: pre\nThis candi\ndate is not publication ready.\nINFO: post",
        "INFO: pre\nThis result is n\not claim eligible.\nINFO: post",
        "publication_ready\nINFO: INFO: false",
        "This candidate is not publication ready.\nINFO: ordinary diagnostic",
        "No formal Evidence Pack was generated.\nINFO: unavailable",
        "false=\nThis candidate is not publication ready.",
        "INFO: checksum finished\nEvidence Pack ID=unavailable",
        "publication_ready=f\nINFO: alse",
        "evidence_class=NON_FORMAL_\nINFO: DEMO",
        "false=\npublication_ready\nfield is reserved",
        "publication_ready\nINFO:\nfalse",
        "Evidence Pack ID\nINFO:\nunavailable",
        "false =\nINFO:\npublication_ready=false",
        "publication_ready\nIN\nFO: false",
        "INF\nO: publication_ready=false",
        "INF\nO: evidence_class=NON_FORMAL_DEMO",
        "INF\nO: Evidence Pack ID=unavailable",
        "evidence_class=NON\n_\nFORMAL_DEMO",
        "NON\n_\nFORMAL_DEMO=evidence_class",
        "Evidence Pack ID=u\nIN\nFO: navailable",
        "Evidence Pack ID=u\nINF\nO: navailable",
        "Evidence Pack ID=u\nINFO: IN\nFO: navailable",
        "\nINFO: INF\nO: false=publication_ready",
        "evidence_class=NON\nINFO: INF\nO: _FORMAL_DEMO",
        "Evidence Pack ID=u\nINFO: INF\nO: navailable",
        "INFO\n:publication_ready=false",
        "INFO:\rINF\nO:publication_ready=false",
        "publication_ready=\rINFO\n: false",
        "IN\n\n\nFO:publication_ready=false",
    ],
)
def test_text_policy_keeps_closed_non_formal_chain_mirrors(value: str) -> None:
    assert contains_formal_claim_escape_text(value) is False


def test_text_policy_has_no_fixed_line_cap_for_formal_reverse_value() -> None:
    value = "FORMAL\n" + ("_\n" * 2_000) + "REGISTRY\nevidence_class"
    assert contains_formal_claim_escape_text(value) is True


def test_text_policy_streams_large_multiline_records_without_changing_result() -> None:
    ordinary = "publication_ready\n" + ("ordinary diagnostic\n" * 5_000)
    repeated = "publication_ready\nfalse\nINFO: next step\n" * 2_000
    assert contains_formal_claim_escape_text(ordinary) is False
    assert contains_formal_claim_escape_text(repeated) is False


@pytest.mark.parametrize("separator", ["\u2028", "\u2029"])
def test_text_policy_rejects_unicode_line_separator_ambiguity(separator: str) -> None:
    assert contains_formal_claim_escape_text(
        f"publication_ready=false\nbe{separator}comes true"
    ) is True


def test_shared_acyclic_container_is_not_mistaken_for_cycle() -> None:
    shared = {"note": "ordinary withheld result"}
    assert contains_formal_claim_escape(
        [shared, shared],
        scan_text_leaves=True,
    ) is False


@pytest.mark.parametrize(
    "value",
    [
        "INFO: publication_ready=false",
        "INFO: claim_eligible=false",
        "INFO: evidence_class=NON_FORMAL_DEMO",
        "Demo result: evidence_class=NON_FORMAL_DEMO",
        "Output policy: publication_ready=false, claim_eligible=false",
        "Metadata: Evidence Pack ID unavailable",
    ],
)
def test_text_policy_allows_standard_log_prefixes(value: str) -> None:
    assert contains_formal_claim_escape_text(value) is False


def test_dense_safe_single_line_policy_records_remain_supported() -> None:
    value = ("publication_ready=false " * 5_000).rstrip()
    assert contains_formal_claim_escape_text(value) is False


def test_formal_records_cannot_escape_by_inserting_one_newline() -> None:
    records = (
        "publication_ready=true",
        "claim_eligible=yes",
        "evidence_pack_allowed=1",
        "evidence_class=FORMAL_REGISTRY",
        "Evidence Pack ID=pack-123",
        "true = publication_ready",
        "FORMAL_REGISTRY = evidence_class",
        "pack-123 = Evidence Pack ID",
        "scientific_verdict=SUPPORTED",
        "publication_ready|true",
        "evidence_class|FORMAL_REGISTRY",
        "Evidence Pack ID|pack-123",
        "true | publication_ready",
        "FORMAL_REGISTRY | evidence_class",
        "pack-123 | Evidence Pack ID",
    )
    for record in records:
        for index in range(1, len(record)):
            wrapped = f"{record[:index]}\n{record[index:]}"
            assert contains_formal_claim_escape_text(wrapped) is True, wrapped


def test_formal_records_cannot_escape_by_inserting_two_newlines() -> None:
    records = (
        "publication_ready=true",
        "claim_eligible=yes",
        "evidence_pack_allowed=1",
        "evidence_class=FORMAL_REGISTRY",
        "Evidence Pack ID=pack-123",
        "true = publication_ready",
        "FORMAL_REGISTRY = evidence_class",
        "pack-123 = Evidence Pack ID",
        "scientific_verdict=SUPPORTED",
        "publication_ready|true",
        "evidence_class|FORMAL_REGISTRY",
        "Evidence Pack ID|pack-123",
        "true | publication_ready",
        "FORMAL_REGISTRY | evidence_class",
        "pack-123 | Evidence Pack ID",
    )
    for record in records:
        for first in range(1, len(record) - 1):
            for second in range(first + 1, len(record)):
                wrapped = (
                    f"{record[:first]}\n{record[first:second]}\n"
                    f"{record[second:]}"
                )
                assert contains_formal_claim_escape_text(wrapped) is True, wrapped


def test_formal_records_cannot_escape_by_inserting_three_newlines() -> None:
    records = (
        "publication_ready=true",
        "claim_eligible=yes",
        "evidence_pack_allowed=1",
        "evidence_class=FORMAL_REGISTRY",
        "Evidence Pack ID=pack-123",
        "true = publication_ready",
        "FORMAL_REGISTRY = evidence_class",
        "pack-123 = Evidence Pack ID",
        "publication_ready|true",
        "evidence_class|FORMAL_REGISTRY",
        "Evidence Pack ID|pack-123",
        "true | publication_ready",
        "FORMAL_REGISTRY | evidence_class",
        "pack-123 | Evidence Pack ID",
    )
    for record in records:
        for first in range(1, len(record) - 2):
            for second in range(first + 1, len(record) - 1):
                for third in range(second + 1, len(record)):
                    wrapped = (
                        f"{record[:first]}\n{record[first:second]}\n"
                        f"{record[second:third]}\n{record[third:]}"
                    )
                    assert contains_formal_claim_escape_text(wrapped) is True, wrapped
