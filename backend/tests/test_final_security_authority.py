"""Attack regressions for server evidence and AI-tool ownership boundaries."""

from __future__ import annotations

import uuid
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.api.chat import ChatMessage, ChatRequest, _build_runtime
from app.models.schemas import ChatSession, DataFile, User
from app.services.ai_tools import (
    _authorize_tool_storage_inputs,
    _execute_tool_inner,
)
from app.services.analysis_validator import (
    PUBLICATION_LANGUAGE_ATTESTATION_KEY,
    build_publication_language_attestation,
    validate_analysis,
    verify_publication_language_attestation,
)
from app.services.claim_validator import (
    enforce_scientific_conclusion_gate,
    scientific_conclusion_scope_violations,
    validate_claims,
)
from app.services.paper_generator import (
    build_reproducibility_manifest,
    generate_reproducibility_appendix,
)
from app.services.server_evidence import build_server_evidence_record


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_strong_cosmology_conclusion_requires_calibrated_significance_evidence():
    claim = "The data favor w0waCDM over LambdaCDM."
    preliminary = [
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "success": True,
                "publication_ready": True,
                "claim_scope": "compressed_likelihood_preliminary",
                "significance_ready": False,
            },
        }
    ]
    assert scientific_conclusion_scope_violations(claim, preliminary)

    manifest_hash = "sha256:" + "a" * 64
    calibrated = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "significance_ready": True,
            "evidence_manifest_sha256": manifest_hash,
            "data_fingerprint": "sha256:" + "b" * 64,
            "likelihood_fingerprint": "sha256:" + "c" * 64,
            "conclusion_attestations": [{
                "schema_version": 1,
                "artifact_type": "scientific_conclusion_attestation",
                "attestation_id": "preference-001",
                "claim_kind": "extended_model_preference",
                "baseline_model": "lcdm",
                "alternative_model": "w0wa_cdm",
                "data_fingerprint": "sha256:" + "b" * 64,
                "likelihood_fingerprint": "sha256:" + "c" * 64,
                "comparison_type": "likelihood_ratio",
                "calibration": {
                    "method": "wilks",
                    "verified": True,
                    "assumptions_verified": True,
                    "likelihood_only_mle_proven": True,
                },
                "manifest_sha256": manifest_hash,
                "publication_ready": True,
                "significance_ready": True,
            }],
        },
    }]
    cited_claim = (
        "The data favor w0waCDM over LambdaCDM [evidence:preference-001]."
    )
    assert not scientific_conclusion_scope_violations(cited_claim, calibrated)
    assert scientific_conclusion_scope_violations(
        "The data favor wCDM over LambdaCDM [evidence:preference-001].",
        calibrated,
    )
    wrong_data_branch = deepcopy(calibrated)
    wrong_data_branch[0]["result"]["data_fingerprint"] = "sha256:" + "d" * 64
    assert scientific_conclusion_scope_violations(cited_claim, wrong_data_branch)
    assert not scientific_conclusion_scope_violations(
        "We test whether dark energy evolves; no evidence is claimed.", preliminary
    )


def test_strong_cosmology_synonyms_do_not_bypass_attestation_gate():
    claims = (
        "The data prefer w0waCDM over LambdaCDM.",
        "The cosmological constant is inconsistent with the observations.",
        "We detect a nonzero time variation in the dark-energy equation of state.",
        "The equation of state evolves with redshift in w0waCDM relative to LambdaCDM.",
        r"The fit finds non-zero $w_a$ in w0waCDM relative to LambdaCDM.",
    )
    assert all(scientific_conclusion_scope_violations(claim, []) for claim in claims)


def test_scientific_conclusion_sentence_scan_is_linear_on_long_spacing():
    reply = (" " * 100_000) + "Dark energy evolves."
    violations = scientific_conclusion_scope_violations(reply, [])
    assert len(violations) == 1
    assert violations[0].match_text == "Dark energy evolves."

    repeated = "Dark energy evolves.\n" * 1_000
    repeated_violations = scientific_conclusion_scope_violations(repeated, [])
    assert len(repeated_violations) == 1_000
    assert repeated_violations[0].line_number == 1
    assert repeated_violations[-1].line_number == 1_000


def test_dark_energy_conclusion_catalogue_handles_common_notation_linearly():
    claims = (
        "Dark energy evolves with redshift.",
        "The fit favors time-varying dark energy.",
        "The equation of state is dynamical.",
        "We detect a nonzero time variation in the dark-energy equation of state.",
        r"The fit finds non-zero $w_a$.",
        r"The fit finds nonzero $w_a$.",
        r"The fit finds $w_a$ is nonzero.",
        r"The result has $w_a \neq 0$.",
        r"The result has $w_a\neq0$.",
        "The result has wₐ ≠ 0.",
    )
    assert all(scientific_conclusion_scope_violations(claim, []) for claim in claims)

    bounded_insertion = "Dark energy " + ("a " * 25) + "evolves."
    assert scientific_conclusion_scope_violations(bounded_insertion, [])

    long_spacing = "Dark energy" + (" " * 100_000) + "evolves."
    assert not scientific_conclusion_scope_violations(long_spacing, [])


def test_decimal_points_do_not_split_scientific_conclusion_scope():
    claims = (
        "Dark energy at z=0.5 evolves.",
        "Dark energy with w_a=-0.8 evolves.",
        "The equation of state at z=0.5 is dynamical.",
    )
    assert all(scientific_conclusion_scope_violations(claim, []) for claim in claims)


def test_expanded_headline_conclusions_require_exact_attestations():
    attacks = (
        "The Hubble tension is resolved.",
        "Spatial curvature is favored over flat cosmology.",
        "A nonzero neutrino mass is detected.",
        "General relativity is ruled out.",
    )
    for claim in attacks:
        violations = scientific_conclusion_scope_violations(claim, [])
        assert violations, claim
        replacement, enforced = enforce_scientific_conclusion_gate(claim, [])
        assert enforced
        assert claim not in replacement
        assert "Scientific conclusion withheld" in replacement

    safe_scope_statements = (
        "The Hubble tension remains unresolved.",
        "The data do not resolve the Hubble tension.",
        "We test whether spatial curvature is favored.",
        "No evidence for a nonzero neutrino mass is detected.",
        "General relativity is not ruled out.",
    )
    assert all(
        not scientific_conclusion_scope_violations(statement, [])
        for statement in safe_scope_statements
    )


def test_chinese_headline_conclusions_cannot_bypass_attestation_gate():
    attacks = (
        "哈勃张力已经解决。",
        "数据支持非零空间曲率。",
        "我们检测到非零中微子质量。",
        "广义相对论已被排除。",
        "暗能量随时间演化。",
        "宇宙学常数被排除。",
    )
    assert all(
        scientific_conclusion_scope_violations(claim, []) for claim in attacks
    )

    safe = (
        "哈勃张力仍未解决。",
        "没有显著证据支持非零空间曲率。",
        "尚未检测到非零中微子质量。",
        "广义相对论未被排除。",
        "我们正在检验暗能量是否随时间演化。",
    )
    assert all(
        not scientific_conclusion_scope_violations(statement, [])
        for statement in safe
    )


def test_hubble_tension_resolution_can_pass_only_with_matching_attestation():
    manifest_hash = "sha256:" + "a" * 64
    attestation_id = "hubble-resolution-001"
    calibrated = [{
        "tool": "run_cobaya_cosmology",
        "result": {
            "success": True,
            "publication_ready": True,
            "significance_ready": True,
            "evidence_manifest_sha256": manifest_hash,
            "data_fingerprint": "sha256:" + "b" * 64,
            "likelihood_fingerprint": "sha256:" + "c" * 64,
            "conclusion_attestations": [{
                "schema_version": 1,
                "artifact_type": "scientific_conclusion_attestation",
                "attestation_id": attestation_id,
                "claim_kind": "hubble_tension_resolution",
                "claim_subject": "hubble_tension",
                "data_fingerprint": "sha256:" + "b" * 64,
                "likelihood_fingerprint": "sha256:" + "c" * 64,
                "comparison_type": "tension_consistency_test",
                "independence_verified": True,
                "resolution_criterion_verified": True,
                "calibration": {
                    "method": "wilks",
                    "verified": True,
                    "assumptions_verified": True,
                    "likelihood_only_mle_proven": True,
                },
                "manifest_sha256": manifest_hash,
                "publication_ready": True,
                "significance_ready": True,
            }],
        },
    }]
    cited = (
        "The Hubble tension is resolved "
        f"[evidence:{attestation_id}]."
    )

    assert not scientific_conclusion_scope_violations(cited, calibrated)
    assert scientific_conclusion_scope_violations(
        "The Hubble tension is resolved [evidence:wrong-branch].",
        calibrated,
    )
    wrong_test = deepcopy(calibrated)
    wrong_test[0]["result"]["conclusion_attestations"][0][
        "comparison_type"
    ] = "likelihood_ratio"
    assert scientific_conclusion_scope_violations(cited, wrong_test)


def test_paper_typed_numeric_gate_rejects_cross_quantity_equal_values():
    attacks = (
        ("H0 = 71.43 km/s/Mpc", {"parallax": 71.43}),
        ("Omega_m = 0.315", {"redshift": 0.315}),
        ("The preference is 5 sigma", {"snr": 5.0}),
        ("p=0.05", {"redshift": 0.05}),
        ("p=0.05", {"P": 0.05}),
        ("Pearson r=0.4", {"redshift": 0.4}),
    )
    for claim, result in attacks:
        validation = validate_claims(
            claim,
            [{"tool": "search_objects", "result": result}],
            require_typed_scientific_match=True,
        )
        assert validation.ok is False, (claim, result)

    positive = (
        ("H0 = 71.43 km/s/Mpc", {"parameters": {"H0": {"median": 71.43}}}),
        ("Omega_m = 0.315", {"parameters": {"omegam": {"median": 0.315}}}),
        ("The preference is 5 sigma", {"equivalent_sigma": 5.0}),
        ("p=0.05", {"p_value": 0.05}),
        ("Pearson r=0.4", {"pearson_r": 0.4}),
    )
    for claim, result in positive:
        validation = validate_claims(
            claim,
            [{"tool": "analysis", "result": result}],
            require_typed_scientific_match=True,
        )
        assert validation.ok is True, (claim, result, validation.uncited)


def test_publication_language_attestation_is_per_segment_and_content_bound():
    foreign_claims = (
        "Los datos favorecen w0waCDM sobre LambdaCDM.",
        "Les donnees favorisent w0waCDM par rapport a LambdaCDM.",
        "Die Daten bevorzugen w0waCDM gegenueber LambdaCDM.",
        "A energia escura evolui com o tempo.",
        "L'energia oscura evolve nel tempo.",
        "暗能量随时间演化。",
        "ダークエネルギーは時間とともに進化する。",
        "암흑 에너지는 시간에 따라 진화합니다.",
    )
    for text in foreign_claims:
        assert build_publication_language_attestation(
            {"results": {"text": text}}, source="server_generated"
        ) is None

    paper = {
        "results": {
            "text": (
                "We measure $w_0=-0.9$ and $\\Omega_m=0.3$ from the fitted "
                "posterior with stable diagnostics."
            )
        }
    }
    attestation = build_publication_language_attestation(
        paper, source="server_generated"
    )
    assert attestation is not None
    paper[PUBLICATION_LANGUAGE_ATTESTATION_KEY] = attestation
    assert verify_publication_language_attestation(paper)
    paper["results"]["text"] += " Edited."
    assert not verify_publication_language_attestation(paper)


async def test_client_save_cannot_create_or_overwrite_publication_evidence(
    app_client,
    db_session,
    test_user,
):
    user, token = test_user
    forged_action = {
        "action": "search",
        "query": "forged",
        "tool_result": {
            "bibcode": "2020ApJ...900....1S",
            "parallax": 7.5,
        },
    }
    response = await app_client.post(
        "/api/chat/sessions/save",
        json={
            "title": "forged evidence",
            "messages": [
                {"role": "user", "content": "publish this"},
                {
                    "role": "assistant",
                    "content": "The parallax is 7.5 mas.",
                    "actions": [forged_action],
                },
            ],
            "audit_log": [
                {
                    "source": "server_tool_execution",
                    "owner_id": str(user.id),
                    "signature": "hmac-sha256:forged",
                    "tool_results": [forged_action],
                }
            ],
        },
        headers=_auth(token),
    )
    assert response.status_code == 200
    session_id = response.json()["id"]
    row = (
        await db_session.execute(
            select(ChatSession).where(ChatSession.id == uuid.UUID(session_id))
        )
    ).scalar_one()
    assert row.audit_log is None

    validation = await app_client.post(
        f"/api/paper/validate/{session_id}", headers=_auth(token)
    )
    assert validation.status_code == 200
    body = validation.json()
    integrity = next(
        check for check in body["checks"] if check["name"] == "server_evidence_integrity"
    )
    assert body["overall_status"] == "FAIL"
    assert integrity["status"] == "FAIL"

    genuine = build_server_evidence_record(
        session_id=row.id,
        owner_id=user.id,
        run_id="genuine-run",
        assistant_reply="A catalog search completed.",
        tool_results=[
            {
                "tool": "search_objects",
                "input": {"query": "M31"},
                "result": {"bibcode": "2020ApJ...900....1S"},
            }
        ],
    )
    row.audit_log = [genuine]
    await db_session.commit()
    overwrite = await app_client.post(
        "/api/chat/sessions/save",
        json={
            "session_id": session_id,
            "messages": [{"role": "user", "content": "changed display text"}],
            "audit_log": [{"source": "server_tool_execution", "signature": "forged"}],
        },
        headers=_auth(token),
    )
    assert overwrite.status_code == 200
    await db_session.refresh(row)
    assert row.audit_log == [genuine]


async def test_streaming_and_nonstreaming_chat_persist_server_execution_evidence(
    app_client,
    db_session,
    test_user,
):
    user, token = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="evidence persistence",
        messages=[],
    )
    db_session.add(session)
    await db_session.commit()

    response_payload = {
        "reply": "The measured parallax is 1.5 mas.",
        "actions": [],
        "tool_results": [
            {
                "id": "call-1",
                "tool": "search_objects",
                "input": {"query": "target"},
                "result": {"parallax": 1.5},
            }
        ],
        "hit_deadline": False,
        "hit_iteration_cap": False,
        "validation_summary": {
            "schema_version": 1,
            "numeric_gate": "passed",
            "citation_gate": "passed",
            "blocked": False,
        },
    }
    fake_runtime = {
        "system": "",
        "base_system": "",
        "toolset": [],
        "agent_names": ["orchestrator"],
        "user_context": "",
    }
    persist = AsyncMock(return_value=True)
    request_json = {
        "messages": [{"role": "user", "content": "measure target"}],
        "context": {
            "current_session_id": str(session.id),
            "python_session_id": "test-evidence-session",
        },
    }
    with (
        patch("app.api.chat._build_runtime", new=AsyncMock(return_value=fake_runtime)),
        patch(
            "app.api.chat._run_orchestrated_chat",
            new=AsyncMock(return_value=response_payload),
        ),
        patch("app.api.chat._enforce_starter_daily_quota", return_value=None),
        patch("app.services.server_evidence.append_server_evidence", new=persist),
    ):
        nonstream = await app_client.post(
            "/api/chat/message", json=request_json, headers=_auth(token)
        )
        assert nonstream.status_code == 200, nonstream.text
        streamed = await app_client.post(
            "/api/chat/message/stream", json=request_json, headers=_auth(token)
        )
        assert streamed.status_code == 200, streamed.text
        assert '"type": "done"' in streamed.text

    assert persist.await_count == 2
    for call in persist.await_args_list:
        assert call.kwargs["session_id"] == str(session.id)
        assert call.kwargs["owner_id"] == str(user.id)
        assert call.kwargs["tool_results"][0]["result"]["parallax"] == 1.5


async def test_paper_numeric_claims_ignore_client_actions_and_use_signed_results(
    db_session,
    test_user,
):
    user, _token = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="numeric authority",
        messages=[
            {
                "role": "assistant",
                "content": "The parallax is 9.9 mas.",
                "actions": [
                    {
                        "action": "search",
                        "tool_result": {"parallax": 9.9},
                    }
                ],
            }
        ],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="numeric-run",
            assistant_reply="The measured parallax is 1.5 mas.",
            tool_results=[
                {
                    "tool": "search_objects",
                    "input": {"query": "target"},
                    "result": {
                        "bibcode": "2020ApJ...900....1S",
                        "parallax": 1.5,
                    },
                }
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()

    rejected = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "The parallax is 9.9 mas."}},
    )
    numeric = next(
        check for check in rejected["checks"] if check["name"] == "numeric_claim_evidence"
    )
    assert numeric["status"] == "FAIL"
    assert rejected["overall_status"] == "FAIL"

    supported = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "The parallax is 1.5 mas."}},
    )
    numeric = next(
        check for check in supported["checks"] if check["name"] == "numeric_claim_evidence"
    )
    assert numeric["status"] == "PASS"


async def test_paper_numeric_universe_excludes_preliminary_and_freeform_results(
    db_session,
    test_user,
):
    user, _token = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="publication numeric scope",
        messages=[],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="preliminary-number-run",
            assistant_reply="An exploratory H0 value was computed.",
            tool_results=[
                {
                    "tool": "run_cosmology_likelihood_chain",
                    "input": {"model": "lcdm"},
                    "result": {
                        "success": True,
                        "publication_ready": False,
                        "preliminary_ready": True,
                        "analysis_status": "EXPLORATORY",
                        "parameters": {"H0": {"median": 71.43}},
                    },
                },
                {
                    "tool": "run_python",
                    "input": {"code": "print(71.43)"},
                    "result": {"success": True, "stdout": "71.43"},
                },
                {
                    "tool": "search_objects",
                    "input": {"query": "M31"},
                    "result": {"bibcode": "2020ApJ...900....1S"},
                },
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()

    validation = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={"results": {"text": "We measure H0 = 71.43 km/s/Mpc."}},
    )
    numeric = next(
        check
        for check in validation["checks"]
        if check["name"] == "numeric_claim_evidence"
    )
    assert numeric["status"] == "FAIL"
    assert validation["overall_status"] == "FAIL"


async def test_paper_blocks_irrelevant_evidence_and_unvalidated_language(
    db_session,
    test_user,
):
    user, _token = test_user
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="conclusion scope authority",
        messages=[],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="m31-only-run",
            assistant_reply="A descriptive M31 catalog lookup completed.",
            tool_results=[
                {
                    "tool": "search_objects",
                    "input": {"query": "M31"},
                    "result": {
                        "bibcode": "2020ApJ...900....1S",
                        "name": "M31",
                    },
                }
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()

    unrelated = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={
            "results": {
                "text": (
                    "Dark energy evolves with redshift, and these results rule out "
                    "the cosmological constant."
                )
            }
        },
    )
    scope = next(
        check
        for check in unrelated["checks"]
        if check["name"] == "methodology_and_conclusion_scope"
    )
    assert scope["status"] == "FAIL"
    assert unrelated["overall_status"] == "FAIL"

    untranslated = await validate_analysis(
        str(session.id),
        db_session,
        owner_id=str(user.id),
        paper_json={
            "results": {
                "text": "我们的分析证明哈勃常数为 71.43 公里每秒每兆秒差距。"
            }
        },
    )
    language = next(
        check for check in untranslated["checks"] if check["name"] == "claim_language"
    )
    assert language["status"] == "FAIL"
    assert untranslated["overall_status"] == "FAIL"

    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="signed-untranslated-run",
            assistant_reply="我们的分析证明哈勃常数为 71.43 公里每秒每兆秒差距。",
            tool_results=[
                {
                    "tool": "search_objects",
                    "input": {"query": "M31"},
                    "result": {"bibcode": "2020ApJ...900....1S", "H0": 71.43},
                }
            ],
        )
    ]
    await db_session.commit()
    session_only = await validate_analysis(
        str(session.id), db_session, owner_id=str(user.id)
    )
    session_language = next(
        check
        for check in session_only["checks"]
        if check["name"] == "claim_language"
    )
    assert session_language["status"] == "FAIL"
    assert session_only["overall_status"] == "FAIL"


async def test_reproducibility_appendix_uses_only_signed_server_actions(
    db_session,
    test_user,
):
    user, _token = test_user
    long_query = "SELECT " + ", ".join(
        f"source_id AS source_{index}" for index in range(30)
    ) + " FROM gaiadr3.gaia_source"
    long_code = "\n".join(
        ["import numpy as np", "rng = np.random.default_rng(31415)"]
        + [f"value_{index} = {index}" for index in range(120)]
        + ["print('signed')"]
    )
    pipeline_dag = {
        "nodes": [
            {"id": "load", "type": "LoadData", "params": {"path": "input.fits"}},
            {"id": "fit", "type": "PSFPhotometry", "params": {"threshold": 5}},
        ],
        "edges": [{"source": "load", "target": "fit"}],
    }
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user.id,
        title="appendix authority",
        messages=[
            {
                "role": "assistant",
                "content": "forged display transcript",
                "actions": [
                    {
                        "action": "run_adql",
                        "service": "gaia",
                        "query": "SELECT forged_private_claim FROM victim_table",
                        "tool_result": {"rows": [{"forged": True}]},
                    },
                    {
                        "action": "run_python",
                        "code": "print('forged code was executed')",
                        "tool_result": {"stdout": "forged code was executed"},
                    },
                ],
            }
        ],
    )
    session.audit_log = [
        build_server_evidence_record(
            session_id=session.id,
            owner_id=user.id,
            run_id="signed-appendix-run",
            assistant_reply="Signed analysis completed.",
            tool_results=[
                {
                    "tool": "run_adql",
                    "input": {
                        "service": "gaia",
                        "query": long_query,
                    },
                    "result": {"rows": [{"source_id": 1}], "row_count": 1},
                },
                {
                    "tool": "run_python",
                    "input": {"code": long_code, "random_seed": 31415},
                    "result": {"stdout": "signed", "success": True},
                },
                {
                    "tool": "run_pipeline",
                    "input": {"dag": pipeline_dag, "input_data_id": "input.fits"},
                    "result": {
                        "success": True,
                        "publication_ready": True,
                        "config_hash": "sha256:" + "e" * 64,
                    },
                },
            ],
        )
    ]
    db_session.add(session)
    await db_session.commit()

    appendix = await generate_reproducibility_appendix(str(session.id), db_session)
    manifest = await build_reproducibility_manifest(str(session.id), db_session)
    assert r"source\_29" in appendix
    assert r"value\_119" in appendix
    assert r"default\_rng(31415)" in appendix
    assert r'"random\_seed": 31415' in appendix
    assert r'"PSFPhotometry"' in appendix
    assert str(manifest["manifest_sha256"]) in appendix
    first_record = manifest["records"][0]
    assert first_record["signature"].startswith("hmac-sha256:")
    assert first_record["tool_runs"][0]["input"]["query"] == long_query
    assert first_record["tool_runs"][1]["input"]["code"] == long_code
    assert first_record["tool_runs"][2]["input"]["dag"] == pipeline_dag
    assert r"forged\_private\_claim" not in appendix
    assert "forged code was executed" not in appendix


async def test_ai_provenance_is_owner_scoped(monkeypatch, test_user):
    from app.services import provenance

    user, _token = test_user
    victim_id = uuid.uuid4()
    saved = list(provenance._provenance_records)
    provenance._provenance_records[:] = [
        {
            "id": "victim-record",
            "entity_type": "pipeline_run",
            "entity_id": "secret-run",
            "activity": "run_adql",
            "params": {"secret_query": "SELECT private"},
            "parent_ids": [],
            "agent": "test",
            "environment": {"packages": {"private": "1.0"}},
            "user_id": str(victim_id),
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
        {
            "id": "own-record",
            "entity_type": "pipeline_run",
            "entity_id": "own-run",
            "activity": "run_adql",
            "params": {"query": "SELECT public"},
            "parent_ids": [],
            "agent": "test",
            "environment": {},
            "user_id": str(user.id),
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr(
        "app.services.durable_research_records.load_provenance",
        lambda *args, **kwargs: [],
    )
    try:
        foreign = await _execute_tool_inner(
            "get_provenance",
            {"entity_id": "secret-run", "action": "reproduce"},
            user_id=str(user.id),
        )
        assert foreign == {
            "error": "Provenance record not found",
            "error_class": "not_found",
        }
        own = await _execute_tool_inner(
            "get_provenance",
            {"entity_id": "own-run", "action": "lineage"},
            user_id=str(user.id),
        )
        assert own["nodes"]
        assert own["nodes"][0]["params"]["query"] == "SELECT public"
    finally:
        provenance._provenance_records[:] = saved


async def test_xray_and_nested_ai_pipeline_paths_are_owner_authorized(
    db_session,
    test_user,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    user, _token = test_user
    victim = User(
        id=uuid.uuid4(),
        username=f"victim-{uuid.uuid4().hex[:8]}",
        email=f"victim-{uuid.uuid4().hex[:8]}@example.test",
        password_hash="not-used",
        subscription_tier="solo",
    )
    own_key = "uploads/own/spectrum.pha"
    victim_key = "uploads/victim/private.fits"
    db_session.add(victim)
    db_session.add_all(
        [
            DataFile(
                user_id=user.id,
                source="upload",
                object_id="spectrum.pha",
                fits_path=own_key,
                metadata_={},
            ),
            DataFile(
                user_id=victim.id,
                source="upload",
                object_id="private.fits",
                fits_path=victim_key,
                metadata_={},
            ),
        ]
    )
    await db_session.commit()

    # Redirect short-lived tool authorization sessions to the fixture DB.
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    with patch("app.models.database.async_session", new=session_factory):
        allowed, error = await _authorize_tool_storage_inputs(
            "x_ray_spectral_fit",
            {"pha_path": own_key, "model": "phabs*powerlaw"},
            user_id=str(user.id),
        )
        assert error is None
        assert allowed["pha_path"] == own_key

        _safe, foreign_error = await _authorize_tool_storage_inputs(
            "x_ray_spectral_fit",
            {"pha_path": victim_key, "model": "phabs*powerlaw"},
            user_id=str(user.id),
        )
        assert foreign_error["error_class"] == "storage_file_not_found"

        _safe, absolute_error = await _authorize_tool_storage_inputs(
            "x_ray_spectral_fit",
            {"pha_path": "/etc/passwd", "model": "phabs*powerlaw"},
            user_id=str(user.id),
        )
        assert absolute_error["error_class"] == "invalid_storage_path"

        hostile = {
            "input_data_id": own_key,
            "dag": {
                "nodes": [
                    {
                        "id": "load",
                        "type": "LoadData",
                        "data": {"params": {"fits_path": victim_key}},
                    }
                ],
                "edges": [],
            },
        }
        original = deepcopy(hostile)
        _safe, nested_error = await _authorize_tool_storage_inputs(
            "run_pipeline", hostile, user_id=str(user.id)
        )
        assert nested_error["error_class"] == "storage_file_not_found"
        assert hostile == original


async def test_disabled_sandbox_is_removed_from_every_model_toolset(
    db_session,
    monkeypatch,
):
    # _build_runtime imports settings locally; patch the shared settings object.
    from app.config import settings

    monkeypatch.setattr(settings, "sandbox_backend", "disabled")
    runtime = {
        "system_prompt": "",
        "tool_names": ["run_python", "search_objects"],
        "agent_names": ["orchestrator"],
        "user_context": "",
    }
    request = ChatRequest(messages=[ChatMessage(role="user", content="analyze M31")])
    with patch(
        "app.api.chat.orchestrator.build_runtime_context",
        new=AsyncMock(return_value=runtime),
    ):
        built = await _build_runtime(request, None, db_session)
    assert "run_python" not in {tool["name"] for tool in built["toolset"]}

    inventory = ChatRequest(messages=[ChatMessage(role="user", content="list all tools")])
    with patch(
        "app.api.chat.orchestrator.build_runtime_context",
        new=AsyncMock(return_value=runtime),
    ):
        built_inventory = await _build_runtime(inventory, None, db_session)
    assert "run_python" not in {
        tool["name"] for tool in built_inventory["toolset"]
    }
