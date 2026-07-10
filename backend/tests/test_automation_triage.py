"""Contract tests for the authenticated automation triage API."""

import pytest

from app.services.automation_triage import triage_cosmology_prompt


EXECUTE_PROMPT = (
    "Run the executable cosmology likelihood chain with datasets pantheon "
    "for models lcdm. Report the posterior value of Omega_m with uncertainty."
)


def test_triage_cosmology_prompt_preserves_existing_execute_contract() -> None:
    result = triage_cosmology_prompt(EXECUTE_PROMPT)

    assert result == {
        "is_cosmo_workflow": True,
        "is_research_detour": False,
        "direct_route": False,
        "run_calls": [
            {
                "tool": "run_cosmology_likelihood_chain",
                "dataset_keys": ["pantheon_plus"],
                "model": "lcdm",
                "supernova_sets": None,
            }
        ],
        "non_executable_keys": [],
        "verdict": "EXECUTE",
    }


@pytest.mark.parametrize(
    ("prompt", "verdict"),
    [
        ("Compare DESI BAO and CMB constraints in LCDM.", "DETOUR"),
        ("Run the SPT-3G CMB likelihood for LCDM.", "NO_RUN"),
        ("Explain stellar metallicity.", "MISS"),
    ],
)
def test_triage_cosmology_prompt_preserves_verdict_precedence(
    prompt: str,
    verdict: str,
) -> None:
    assert triage_cosmology_prompt(prompt)["verdict"] == verdict


def test_triage_cosmology_prompt_surfaces_direct_route_without_runtime_id() -> None:
    result = triage_cosmology_prompt(
        "Explain the Hubble tension and compare Planck and SH0ES."
    )

    assert result["direct_route"] is True
    assert all("id" not in call for call in result["run_calls"])


async def test_automation_triage_api_requires_authentication(app_client) -> None:
    response = await app_client.post(
        "/api/automation/cosmology/triage",
        json={"prompt": EXECUTE_PROMPT},
    )

    assert response.status_code == 401


async def test_automation_triage_api_returns_stable_contract(
    app_client,
    test_user,
) -> None:
    _, token = test_user
    response = await app_client.post(
        "/api/automation/cosmology/triage",
        json={"prompt": EXECUTE_PROMPT},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == triage_cosmology_prompt(EXECUTE_PROMPT)


async def test_automation_triage_api_caps_prompt_size(
    app_client,
    test_user,
) -> None:
    _, token = test_user
    response = await app_client.post(
        "/api/automation/cosmology/triage",
        json={"prompt": "x" * 50_001},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
