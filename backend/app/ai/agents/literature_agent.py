from app.ai.agents.base import SpecialistAgent

LITERATURE_AGENT = SpecialistAgent(
    name="literature_agent",
    system_prompt=(
        "You are the Standard Astro Literature Agent. Focus on ADS and arXiv discovery, concise paper summaries, "
        "citation hygiene, prior-work comparisons, and identifying the state of the field."
    ),
    tool_names=[
        "search_literature",
        "read_arxiv_paper",
        "extract_literature_tables",
        "generate_paper_draft",
    ],
)
