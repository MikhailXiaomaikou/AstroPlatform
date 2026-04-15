"""Golden-set tests for Orchestrator.classify_intent.

Plan Tier B1: lock in the routing behavior on a 30-query hand-written
suite so regressions to the regex classifier (or any future LLM
fallback) become visible in CI instead of at demo time.

Each query declares which agents MUST appear in the classification.
Classifier may return additional agents; we only assert the required
ones are present. The goal isn't 100% purity — it's preventing drift.
"""

import pytest

from app.ai.orchestrator import Orchestrator


@pytest.fixture
def orch():
    return Orchestrator()


# ---------------------------------------------------------------------------
# Golden set: (query, must_contain)
# ---------------------------------------------------------------------------
GOLDEN: list[tuple[str, set[str]]] = [
    # --- pure data retrieval ---
    ("Query Gaia DR3 for stars within 2 degrees of NGC 1647", {"data_agent"}),
    ("Run an ADQL cone search around RA=71.5 Dec=19.1 r=0.5", {"data_agent"}),
    ("Search SIMBAD for M31", {"data_agent"}),
    ("Download the SDSS spectrum for this quasar", {"data_agent"}),
    ("Cone search the archive for supernova hosts", {"data_agent"}),

    # --- pure analysis ---
    ("Fit an isochrone to the cluster CMD", {"analysis_agent"}),
    ("Run a python script to compute the correlation", {"analysis_agent"}),
    ("Analyze the residuals of this model", {"analysis_agent"}),
    ("Build a regression between mass and metallicity", {"analysis_agent"}),
    ("Validate this paper's reported age", {"analysis_agent"}),

    # --- pure literature ---
    ("Find recent ADS papers on Pleiades rotation", {"literature_agent"}),
    ("Search arxiv for tidal streams in the halo", {"literature_agent"}),
    ("Show citations for the NGC 6397 distance measurement", {"literature_agent"}),
    ("What does prior work say about this binary?", {"literature_agent"}),

    # --- pure observation planning ---
    ("When is NGC 300 observable from Paranal tonight?", {"observation_agent"}),
    ("Compute the airmass curve for this target", {"observation_agent"}),
    ("Draft a telescope proposal to observe SN 2024abc", {"observation_agent"}),
    ("Recommend followup observations for this transient", {"observation_agent"}),
    ("What exposure time do I need for G=18?", {"observation_agent"}),

    # --- pure visualization ---
    ("Plot the HR diagram", {"visualization_agent"}),
    ("Make a BPT figure with the emission-line ratios", {"visualization_agent"}),
    ("Visualize the SED", {"visualization_agent"}),
    ("Draw a lightcurve for this eclipsing binary", {"visualization_agent"}),

    # --- composite: should hit ≥ 2 agents ---
    ("Query Gaia DR3 then fit the cluster isochrone",
     {"data_agent", "analysis_agent"}),
    ("Search SIMBAD and plot the HR diagram",
     {"data_agent", "visualization_agent"}),
    ("ADQL cone search and model the CMD",
     {"data_agent", "analysis_agent"}),
    ("Pull SDSS photometry and analyze the BPT diagram",
     {"data_agent", "analysis_agent"}),
    ("Search for papers then fit the data",
     {"literature_agent", "analysis_agent"}),
    ("Plan observations and prepare a proposal with literature context",
     {"observation_agent", "literature_agent"}),

    # --- fallback: ambiguous queries should still return something ---
    ("Help me understand this dataset", {"data_agent"}),
]


class TestClassifierGoldenSet:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("query", "required"), GOLDEN)
    async def test_query_routes_to_expected_agents(self, orch, query, required):
        agents = set(await orch.classify_intent(query))
        missing = required - agents
        assert not missing, (
            f"Router dropped required agents {missing} for query: {query!r}\n"
            f"Got: {agents}"
        )

    @pytest.mark.asyncio
    async def test_overall_precision(self, orch):
        """Hard floor: ≥ 90% of golden queries must match their required set."""
        hits = 0
        for query, required in GOLDEN:
            agents = set(await orch.classify_intent(query))
            if required <= agents:
                hits += 1
        precision = hits / len(GOLDEN)
        assert precision >= 0.90, (
            f"Router precision {precision:.0%} below 90% floor "
            f"({hits}/{len(GOLDEN)} golden queries matched)"
        )

    @pytest.mark.asyncio
    async def test_classifier_always_returns_at_least_one_agent(self, orch):
        for pathological in ["", "   ", "!@#$%", "hello world", "foo bar baz"]:
            agents = await orch.classify_intent(pathological)
            assert len(agents) >= 1, f"Empty classification for {pathological!r}"
