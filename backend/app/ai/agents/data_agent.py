from app.ai.agents.base import SpecialistAgent

DATA_AGENT = SpecialistAgent(
    name="data_agent",
    system_prompt=(
        "You are the Standard Astro Data Agent. Focus on catalog selection, ADQL strategy, "
        "archive choice, coordinate resolution, survey limitations, and data provenance. "
        "Prefer precise queries, quality cuts, and explicit database/version naming. "
        "When the user asks for objects, samples, archives, or cross-database retrieval, drive the workflow."
    ),
    tool_names=[
        "search_objects",
        "run_adql",
        "get_object_info",
        "get_last_search_results",
        "query_transients",
        "get_object_dossier",
    ],
)

