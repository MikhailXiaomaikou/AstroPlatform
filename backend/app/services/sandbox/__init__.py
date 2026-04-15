"""Sandbox execution backends for run_python.

The default backend remains in-process (see code_executor.execute_python)
for speed and full session persistence. The subprocess backend provides
stability isolation: OOM, infinite loops, and SIGSEGV in user code
cannot crash the FastAPI worker.

Select via settings.sandbox_backend ("inprocess" | "subprocess").
"""

from app.services.sandbox.base import ExecutionBackend, SandboxResult

__all__ = ["ExecutionBackend", "SandboxResult"]
