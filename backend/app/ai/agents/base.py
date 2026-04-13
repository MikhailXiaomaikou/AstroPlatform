from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SpecialistAgent:
    name: str
    system_prompt: str
    tool_names: list[str]

