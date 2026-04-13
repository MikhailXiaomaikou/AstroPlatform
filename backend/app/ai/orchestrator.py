"""Multi-agent orchestrator for Standard Astro."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.ai.agents.analysis_agent import ANALYSIS_AGENT
from app.ai.agents.base import SpecialistAgent
from app.ai.agents.data_agent import DATA_AGENT
from app.ai.agents.literature_agent import LITERATURE_AGENT
from app.ai.agents.observation_agent import OBSERVATION_AGENT
from app.ai.agents.visualization_agent import VISUALIZATION_AGENT
from app.services.event_collector import event_collector
from app.services.memory_service import memory_service

logger = logging.getLogger(__name__)


@dataclass
class AgentHandoff:
    source_agent: str
    target_agent: str
    context_summary: str
    data_references: list[str]
    instruction: str


class Orchestrator:
    def __init__(self):
        self.agents: dict[str, SpecialistAgent] = {
            "data_agent": DATA_AGENT,
            "analysis_agent": ANALYSIS_AGENT,
            "literature_agent": LITERATURE_AGENT,
            "observation_agent": OBSERVATION_AGENT,
            "visualization_agent": VISUALIZATION_AGENT,
        }
        self.intent_patterns = {
            "data_agent": [r"\b(query|catalog|gaia|sdss|simbad|adql|search|cone|archive)\b"],
            "analysis_agent": [r"\b(fit|analy[sz]e|correlation|regression|python|pipeline|model|validate|paper)\b"],
            "literature_agent": [r"\b(literature|paper|ads|arxiv|citation|prior work)\b"],
            "observation_agent": [r"\b(observation|proposal|exposure|visibility|airmass|follow-up|followup|transient)\b"],
            "visualization_agent": [r"\b(plot|figure|visuali[sz]e|diagram|lightcurve|sed|hr diagram|bpt)\b"],
        }

    def _collapse_fast_path(self, agents: list[str], user_message: str) -> tuple[list[str], str | None]:
        exploratory_agents = {"data_agent", "analysis_agent", "visualization_agent"}
        selected = [agent for agent in agents if agent in exploratory_agents]
        if not selected:
            return agents, None
        if set(agents).issubset(exploratory_agents) and ("analysis_agent" in selected or "visualization_agent" in selected):
            return ["analysis_agent"], (
                "Fast path enabled: direct catalog-to-analysis workflows should run in one analysis pass "
                "with data and plotting tools available, instead of serial specialist handoffs."
            )
        return agents, None

    async def classify_intent(self, user_message: str, conversation_history: list[dict] | None = None) -> list[str]:
        message = user_message.lower()
        matched: list[str] = []
        for agent_name, patterns in self.intent_patterns.items():
            if any(re.search(pattern, message) for pattern in patterns):
                matched.append(agent_name)
        if not matched:
            matched = ["data_agent"]
        if len(matched) > 3:
            matched = matched[:3]
        return matched

    async def build_runtime_context(self, user_message: str, conversation_history: list[dict], user_id, db) -> dict:
        classified_agents = await self.classify_intent(user_message, conversation_history)
        agents, fast_path_note = self._collapse_fast_path(classified_agents, user_message)
        user_context = ""
        if user_id:
            try:
                user_context = await memory_service.get_user_context(user_id, user_message, db)
            except Exception as exc:
                logger.warning("Failed to load memory context: %s", exc)
        for agent_name in agents:
            await event_collector.track(
                "ai.agent_routed",
                {
                    "agent_name": agent_name,
                    "intent_classified": agent_name.replace("_agent", ""),
                    "confidence": 0.75 if len(agents) == 1 else 0.6,
                },
                user_id=str(user_id) if user_id else None,
            )

        selected_agents = [self.agents[name] for name in agents if name in self.agents]
        tool_source_agents = classified_agents if fast_path_note else agents
        tool_names = sorted({
            tool
            for name in tool_source_agents
            for tool in (self.agents[name].tool_names if name in self.agents else [])
        })
        system_parts = [
            "You are the Standard Astro Orchestrator coordinating specialist astronomy agents.",
            "If multiple specialties are relevant, produce one coherent answer and avoid duplicate work.",
        ]
        if fast_path_note:
            system_parts.append(fast_path_note)
        if user_context:
            system_parts.append("User Background:\n" + user_context)
        for agent in selected_agents:
            system_parts.append(f"[{agent.name}] {agent.system_prompt}")
        return {
            "agent_names": [agent.name for agent in selected_agents],
            "tool_names": tool_names,
            "system_prompt": "\n\n".join(system_parts),
            "user_context": user_context,
        }

    async def summarize_handoff(self, source_agent: str, target_agent: str, text: str) -> AgentHandoff:
        summary = text[:400]
        return AgentHandoff(
            source_agent=source_agent,
            target_agent=target_agent,
            context_summary=summary,
            data_references=[],
            instruction=f"Continue the workflow with emphasis on {target_agent}.",
        )

    def get_agent_runtime(self, agent_name: str, user_context: str = "") -> dict:
        agent = self.agents.get(agent_name)
        if agent is None:
            return {
                "agent_name": "orchestrator",
                "tool_names": [],
                "system_prompt": "You are the Standard Astro Orchestrator. Coordinate the available tools carefully.",
            }
        parts = [
            f"You are the Standard Astro specialist agent `{agent.name}`.",
            agent.system_prompt,
            "Execute the parts of the workflow that match your specialty. Avoid repeating work already completed by another agent.",
        ]
        if user_context:
            parts.append("User Background:\n" + user_context)
        return {
            "agent_name": agent.name,
            "tool_names": list(agent.tool_names),
            "system_prompt": "\n\n".join(parts),
        }

    async def merge_responses(self, agent_results: list[dict]) -> str:
        seen: set[str] = set()
        parts: list[str] = []
        for result in agent_results:
            text = str(result.get("reply", "") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            parts.append(text)
        return "\n\n".join(parts).strip()


orchestrator = Orchestrator()
