from app.ai.agents.base import SpecialistAgent

OBSERVATION_AGENT = SpecialistAgent(
    name="observation_agent",
    system_prompt=(
        "You are the Standard Astro Observation Agent. Focus on follow-up planning, telescope/instrument selection, "
        "visibility, airmass, transients, scheduling, and proposal preparation."
    ),
    tool_names=[
        "generate_proposal",
        "query_transients",
        "get_followup_recommendation",
    ],
)

