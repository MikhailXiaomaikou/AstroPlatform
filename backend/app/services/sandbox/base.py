"""Abstract base for sandbox execution backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SandboxResult:
    """Result of executing user code in an isolated environment."""

    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    figures: list[str] = field(default_factory=list)  # base64 PNG
    variables: dict[str, str] = field(default_factory=dict)
    variable_types: dict[str, str] = field(default_factory=dict)
    success: bool = True
    # Diagnostic fields populated by the backend
    backend: str = "unknown"
    duration_ms: int = 0
    exit_code: int | None = None


class ExecutionBackend(Protocol):
    """Protocol every sandbox backend must satisfy."""

    name: str

    def execute(self, code: str, *, timeout: int, memory_bytes: int) -> SandboxResult:
        """Run `code` with enforced wall-clock and memory limits."""
        ...
