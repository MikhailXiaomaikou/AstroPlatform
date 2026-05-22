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
        "run_sdss_sql",
        "query_high_velocity_stars",
        "search_lightcurve",
        "get_object_info",
        "get_last_search_results",
        "query_transients",
        "get_object_dossier",
        # Solar-system data retrieval tools. These are still hidden when
        # ASTRO_RESEARCH_FOCUS is not solar_system because chat.py applies the
        # focus manifest as the outer tool gate. Keeping them here lets the
        # data agent expose the right tools when the solar module is active.
        "query_mpc_orbit",
        "fetch_horizons_ephemeris",
        "query_sbdb_orbit",
        "query_sbdb_close_approaches",
        "query_sentry_risk",
        "query_damit_shape_model",
    ],
)
