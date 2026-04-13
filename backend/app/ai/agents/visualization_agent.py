from app.ai.agents.base import SpecialistAgent

VISUALIZATION_AGENT = SpecialistAgent(
    name="visualization_agent",
    system_prompt=(
        "You are the Standard Astro Visualization Agent. Focus on publication-quality figures, clear encodings, "
        "HR/BPT/SED/light-curve plots, and interactive vs static plot tradeoffs."
    ),
    tool_names=[
        "run_python",
        "generate_pipeline",
    ],
)
