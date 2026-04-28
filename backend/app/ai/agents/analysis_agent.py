from app.ai.agents.base import SpecialistAgent

ANALYSIS_AGENT = SpecialistAgent(
    name="analysis_agent",
    system_prompt=(
        "You are the Standard Astro Analysis Agent. Focus on statistical rigor, model fitting, "
        "error propagation, reproducible Python analysis, pipeline design, and quantitative claims. "
        "Use validation-minded language and prefer concrete outputs."
    ),
    tool_names=[
        "search_literature",
        "extract_literature_tables",
        "fit_line_lfr",
        "compare_luminosity_distances",
        "demagnify_sample",
        "export_sample_table",
        "run_python",
        "fit_isochrone",
        "estimate_photo_z",
        "analyze_cross_wavelength",
        "generate_pipeline",
        "run_pipeline",
        "validate_analysis",
        "generate_paper_draft",
        "reduce_ccd_image",
        "solve_astrometry",
        "extract_photometry",
        "extract_sources",
    ],
)
