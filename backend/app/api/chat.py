"""AI assistant backed by the inference router and multi-agent orchestrator."""

import asyncio
import contextlib
import inspect
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.inference_router import InferenceError, inference_router
from app.ai.model_profiles import (
    DEFAULT_AI_PROVIDER,
    DEFAULT_MODEL_BY_PROVIDER,
    ModelProfile,
    all_model_profiles,
    available_model_profiles,
    resolve_model_profile,
)
from app.ai.orchestrator import orchestrator
from app.auth import get_current_user, get_optional_user
from app.api.auth import require_admin_any
from app.rate_limit import limiter
from app.models.database import get_db
from app.models.schemas import User
from app.services.prompt_loader import build_allowed_tools, build_system_prompt
from app.services.agent_session_state import update_session_status as _update_chat_session_status

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

SSE_PREAMBLE_PADDING_BYTES = 8192


# ── Research-focus tool gating (M1 Phase 3, 2026-05-18) ────────────────
#
# Per ASTRO_RESEARCH_FOCUS env (default "cosmology"), the platform filters
# the tool list before it reaches the LLM so non-focus tools are physically
# invisible. SYSTEM_PROMPT itself is also focus-aware: loaded from
# backend/app/prompts/ (three-layer: base.md + core/ + modules/), so
# cosmology-focus prompts no longer include dormant domain idioms (X-ray
# spectroscopy / Pulsar / Solar system / etc.).
#
# Old design (pre-M1): inline 1856-line SYSTEM_PROMPT constant + frozenset
# allowlist + COSMOLOGY_FOCUS_APPENDIX appended at module init. All of that
# lives in prompts/ now and is assembled by prompt_loader.

_ASTRO_RESEARCH_FOCUS = os.getenv("ASTRO_RESEARCH_FOCUS", "cosmology").strip().lower()

# Foci that trigger L1 hard tool gating.  To add a new active module:
# (1) add the focus literal here, (2) add a branch in prompt_loader._active_module_names,
# (3) write the tools list in modules/<name>/manifest.yaml. Karpathy's rule of three:
# now at the 3rd active module (exoplanet, 2026-05-20) — abstraction decision
# deferred to next iteration to avoid mid-M0 refactor; collect more usage data first.
_FOCUS_GATED_VALUES = frozenset({"cosmology", "solar_system", "exoplanet"})


def _filter_tools_by_research_focus(tools: list[dict]) -> list[dict]:
    """L1 hard tool gating per ASTRO_RESEARCH_FOCUS env.

    Foci in ``_FOCUS_GATED_VALUES`` trigger manifest-driven tool whitelist filtering.
    Other values ("all", "", "stellar", typos, etc.) are no-ops — only literal
    active module names enter the filtering path.
    """
    if _ASTRO_RESEARCH_FOCUS not in _FOCUS_GATED_VALUES:
        return tools
    allowed = build_allowed_tools(_ASTRO_RESEARCH_FOCUS)
    return [t for t in tools if t.get("name") in allowed]


# Module-level SYSTEM_PROMPT — assembled from prompts/ at import time,
# cached via @lru_cache inside prompt_loader.  Reload backend to pick up
# prompt.md edits.
SYSTEM_PROMPT = build_system_prompt(_ASTRO_RESEARCH_FOCUS)

# Backward-compat alias for tests that introspect the active allowlist
# (test_research_focus_gating.py asserts ghost-tool absence).  Only set
# under cosmology focus to preserve the original test contract.
if _ASTRO_RESEARCH_FOCUS == "cosmology":
    _COSMOLOGY_FOCUS_TOOL_ALLOWLIST = build_allowed_tools("cosmology")
else:
    _COSMOLOGY_FOCUS_TOOL_ALLOWLIST = frozenset()

def _generate_next_steps(tool_results: list[dict]) -> str:
    """Analyze tool results and generate suggested next steps for the AI to offer."""
    if not tool_results:
        return ""

    suggestions = []
    for result in tool_results:
        if not isinstance(result, dict):
            continue

        # Spectral data detected
        if "wavelength" in result and "flux" in result:
            suggestions.append("Fit emission/absorption lines in this spectrum")
            suggestions.append("Estimate the redshift from spectral features")
            suggestions.append("Measure equivalent widths of key lines")

        # Search results
        if "results" in result and isinstance(result.get("results"), list) and len(result.get("results", [])) > 0:
            n = len(result["results"])
            if n > 1:
                suggestions.append(f"Plot these {n} objects (HR diagram, sky distribution, etc.)")
                suggestions.append("Cross-match with another catalog (Gaia, SDSS, etc.)")
            suggestions.append("Get a detailed dossier on the most interesting object")

        # Fitted parameters
        if "fitted_params" in result or "parameter_summary" in result:
            suggestions.append("Validate assumptions before drafting a report")
            suggestions.append("Run a sensitivity analysis on the fitted parameters")
            suggestions.append("Export results as a Jupyter notebook")

        # Light curve
        if "time" in result and "flux" in result:
            suggestions.append("Search for periodicity (Lomb-Scargle or BLS)")
            suggestions.append("Detrend with Gaussian Process and look for transits/flares")

        # Photo-z
        if "z_phot" in result or "pz_values" in result:
            suggestions.append("Compare photo-z with spectroscopic redshift if available")
            suggestions.append("Plot the P(z) probability distribution")

        # Pipeline run
        if "run_id" in result:
            suggestions.append("Download the publication package (notebook + CSV + provenance)")

        # Image
        if "output_path" in result and any(k in result for k in ["shape", "coverage_fraction"]):
            suggestions.append("Extract sources from this image")
            suggestions.append("Run aperture photometry on detected sources")

    if not suggestions:
        return ""

    # Deduplicate and limit
    seen = set()
    unique = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return "\n".join(f"- {s}" for s in unique[:6])


# T6 (PART T): per-message + total payload size limits.  Without them a
# malicious user can push 1 MB prompts through the chat endpoint and drive
# LLM token spend + inference latency; rate limit alone (15/min) still
# allows 15 MB/min of LLM input.
_CHAT_MESSAGE_MAX_LEN = 50_000
_CHAT_TOTAL_MAX_LEN = 200_000
_CHAT_MAX_MESSAGES = 200


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str = Field(..., max_length=_CHAT_MESSAGE_MAX_LEN)
    actions: list[dict] | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., max_length=_CHAT_MAX_MESSAGES)
    context: dict | None = None  # optional context like current workspace files

    @field_validator("messages")
    @classmethod
    def _check_total_content_size(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        total = sum(len(m.content or "") for m in v)
        if total > _CHAT_TOTAL_MAX_LEN:
            raise ValueError(
                f"total message content {total} bytes exceeds "
                f"{_CHAT_TOTAL_MAX_LEN} byte limit"
            )
        return v


class ChatAction(BaseModel):
    action: str
    params: dict


class ChatResponse(BaseModel):
    reply: str
    actions: list[dict] = []


def _normalize_messages(messages: list[ChatMessage]) -> list[dict]:
    return [{"role": message.role, "content": message.content} for message in messages]


def _safe_context(context: dict | None) -> dict:
    if not context:
        return {}
    return {key: value for key, value in context.items() if key not in {"api_key", "api_keys", "api_provider"}}


def _env_flag_enabled(name: str, *, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _server_deepseek_api_key() -> str:
    """Server-funded DeepSeek key for public/anonymous chat.

    Anthropic/OpenAI remain BYOK.  DeepSeek is intentionally the only hosted
    shared provider because it is the low-cost public fallback we can rate-limit
    centrally without exposing the secret to the browser.
    """

    env_flag = os.getenv("SHARED_DEEPSEEK_API_KEY_ENABLED")
    try:
        from app.config import settings

        settings_enabled = bool(getattr(settings, "shared_deepseek_api_key_enabled", True))
        settings_key = (
            str(getattr(settings, "platform_deepseek_api_key", "") or "").strip()
            or str(getattr(settings, "deepseek_api_key", "") or "").strip()
        )
    except Exception:
        settings_enabled = True
        settings_key = ""
    if env_flag is not None:
        if not _env_flag_enabled("SHARED_DEEPSEEK_API_KEY_ENABLED", default=True):
            return ""
    elif not settings_enabled:
        return ""
    return (
        os.getenv("PLATFORM_DEEPSEEK_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
        or settings_key
    )


def _provider_api_keys(context: dict | None, user: User | None) -> dict[str, str]:
    keys: dict[str, str] = {}
    if user and isinstance(user.api_keys, dict):
        keys.update({str(k): str(v) for k, v in user.api_keys.items() if v})
    if user and user.anthropic_api_key and "anthropic" not in keys:
        keys["anthropic"] = user.anthropic_api_key
    context_api_keys = (context or {}).get("api_keys")
    if isinstance(context_api_keys, dict):
        keys.update({str(k): str(v) for k, v in context_api_keys.items() if v})

    context_key = str((context or {}).get("api_key") or "").strip()
    context_provider = str((context or {}).get("api_provider") or "").strip().lower()
    if context_key:
        if context_provider in {"anthropic", "openai", "deepseek", "local"}:
            keys[context_provider] = context_key
        elif context_key.startswith("sk-ant-"):
            keys["anthropic"] = context_key
        else:
            # Legacy fallback: the generic api_key field was historically used
            # for the primary hosted backend. Treat untyped keys as OpenAI-style.
            keys.setdefault("openai", context_key)
    if "deepseek" not in keys:
        server_key = _server_deepseek_api_key()
        if server_key:
            keys["deepseek"] = server_key
    # Anthropic/OpenAI never fall back to platform env keys: they remain BYOK.
    return keys


def _preferred_backend(context: dict | None) -> str | None:
    provider = str((context or {}).get("api_provider") or "").strip().lower()
    provider_to_backend = {
        "anthropic": "claude",
        "openai": "openai",
        "deepseek": "deepseek",
        "local": "local",
    }
    return provider_to_backend.get(provider)


def _preferred_model_profile(context: dict | None) -> ModelProfile | None:
    provider = str((context or {}).get("api_provider") or "").strip().lower()
    if provider not in {"anthropic", "openai", "deepseek", "local"}:
        return None
    requested = (
        (context or {}).get("model_profile")
        or (context or {}).get("ai_model_profile")
        or (context or {}).get("model")
    )
    return resolve_model_profile(provider, str(requested) if requested is not None else None)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_DEFAULT_WORKFLOW_BUDGET = {
    "mode": "default",
    "agent_loop_seconds": 360.0,
    "endpoint_timeout_seconds": 420.0,
    "summary_reserve_seconds": 60.0,
    "soft_reminder_seconds": 75.0,
    "max_iterations": 12,
    "tool_deadline_scale": 1.0,
}
_LONG_WORKFLOW_BUDGET = {
    "mode": "long",
    # P1.4 (2026-05-22): long mode meaningfully wider now that long-running
    # tools off-load to the async-tool runtime (P1.2) and SSE drops can
    # resume from workflow_checkpoint (P1.3). 30 min wall-clock is enough
    # to walk a 6-stage cosmology research flow with poll-and-continue
    # iterations, without occupying real loop seconds for the slow tool.
    "agent_loop_seconds": 1800.0,
    "endpoint_timeout_seconds": 1920.0,
    "summary_reserve_seconds": 120.0,
    "soft_reminder_seconds": 240.0,
    "max_iterations": 30,
    "tool_deadline_scale": 2.0,
}


def _context_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "long", "extended"}
    return False


def _latest_user_text(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content or ""
    return ""


def _line_fit_method_from_prompt(text: str) -> str:
    """Choose the requested LFR fit method from user wording."""
    lowered = str(text or "").lower()
    if any(
        token in lowered
        for token in (
            "bayesian",
            "贝叶斯",
            "errors-in-both",
            "both axes",
            "两轴",
            "xyerr",
            "measurement error",
            "测量误差",
        )
    ):
        return "bayesian_xyerr"
    return "auto"


def _line_fit_cosmology_from_prompt(text: str) -> str | None:
    """Map explicit paper-cosmology wording to a fit_line_lfr cosmology spec."""
    compact = (
        str(text or "")
        .lower()
        .replace(" ", "")
        .replace("ω", "omega")
        .replace("Ω", "omega")
        .replace("ₘ", "m")
    )
    if "riess" in compact and "suzuki" in compact:
        return "FlatLambdaCDM_H73p8_Om0p295"
    if (
        ("h0=73.8" in compact or "h0:73.8" in compact or "h73p8" in compact)
        and ("om0=0.295" in compact or "omegam=0.295" in compact or "om0p295" in compact)
    ):
        return "FlatLambdaCDM_H73p8_Om0p295"
    if "riess22" in compact or "riess+22" in compact:
        return "riess22_shoes"
    return None


def _line_fit_subsample_splits_from_prompt(text: str) -> list[dict[str, float | str]] | None:
    compact = str(text or "").lower().replace(" ", "")
    wants_z1_split = any(
        token in compact
        for token in (
            "z<1",
            "z＞1",
            "z>1",
            "redshiftdependence",
            "redshift依赖",
            "红移依赖",
        )
    )
    if not wants_z1_split:
        return None
    return [
        {"name": "z<1", "z_min": 0.0, "z_max": 1.0},
        {"name": "z>=1", "z_min": 1.0},
    ]


def _infer_workflow_budget_mode(req: ChatRequest) -> str:
    """Keep ordinary chats cheap; opt into long budget for paper-scale work."""
    context = req.context or {}
    explicit = (
        context.get("workflow_budget_mode")
        or context.get("budget_mode")
        or context.get("workflow_budget")
    )
    if isinstance(explicit, str) and explicit.strip().lower() in {"long", "extended", "elastic"}:
        return "long"
    if any(_context_truthy(context.get(key)) for key in ("long_task", "extended_budget", "elastic_budget")):
        return "long"

    latest = _latest_user_text(req.messages).lower()
    long_task_keywords = (
        "复现", "论文", "长任务", "完整跑", "逃逸速度", "光度函数",
        "reproduce", "replication", "paper", "end-to-end", "long analysis",
        "luminosity function", "escape velocity", "hd 189733", "pleiades cmd",
        "milky way v_esc", "sdss lf",
        # Paper-class line-relation workflows need literature search,
        # table extraction, cosmology conversion, and fitting in one turn.
        # Keep the user's test prompt on the same UI path while giving the
        # agent the budget it already gets in direct long-mode regressions.
        "lfr", "[cii]", "log fwhm", "line width", "bayesian linear regression",
        "intrinsic scatter", "demagnify",
    )
    return "long" if any(keyword in latest for keyword in long_task_keywords) else "default"


def _workflow_budget_config(mode: str | None) -> dict[str, Any]:
    if str(mode or "").strip().lower() in {"long", "extended", "elastic"}:
        return dict(_LONG_WORKFLOW_BUDGET)
    return dict(_DEFAULT_WORKFLOW_BUDGET)


def _debug_stream_enabled(request: Request | None, context: dict | None) -> bool:
    if request is not None and request.query_params.get("debug_stream") == "1":
        return True
    return _context_truthy((context or {}).get("debug_stream"))


def _checkpoint_session_id(chat_session_id: str | None, python_session_id: str | None) -> str | None:
    chat_id = str(chat_session_id or "").strip()
    if chat_id:
        return chat_id
    py_id = str(python_session_id or "").strip()
    if py_id and py_id != "default":
        return py_id
    return None


def _hash_tool_input(tool_input: Any) -> str:
    import hashlib

    try:
        raw = json.dumps(tool_input, sort_keys=True, default=str)
    except (TypeError, ValueError) as e:
        logger.debug("hash_tool_input json fallback (%s): %s", type(e).__name__, e)
        raw = str(tool_input)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _checkpoint_cache_refs(tool_name: str, result: Any, python_session_id: str) -> list[str]:
    refs: list[str] = []
    if tool_name in {"run_adql", "query_high_velocity_stars"}:
        refs.extend(["latest_adql", "latest_adql_set", "latest_adql_sets"])
    elif tool_name == "run_sdss_sql":
        refs.extend(["latest_sdss_sql", "latest_adql", "latest_adql_set"])
    elif tool_name in {"search_objects", "get_object_info", "get_object_dossier"}:
        refs.append("latest")
    elif tool_name == "search_lightcurve":
        refs.append("latest_lightcurve")
    elif tool_name == "run_python":
        refs.append(f"python_session:{python_session_id}")
    if isinstance(result, dict) and result.get("figures"):
        refs.append("figures")
    return refs


def _checkpoint_status(result: Any) -> str:
    if not isinstance(result, dict):
        return "completed"
    status = str(result.get("__tool_status__") or result.get("analysis_status") or "").upper()
    if result.get("success") is False or result.get("error") or status in {"FAILED", "UNAVAILABLE"}:
        return "failed"
    return "completed"


def _checkpoint_result_summary(tool_name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return f"{tool_name} returned {type(result).__name__}"
    bits: list[str] = []
    row_count = result.get("row_count")
    if isinstance(row_count, int):
        bits.append(f"{row_count} rows")
    columns = result.get("columns")
    if isinstance(columns, list) and columns:
        bits.append("columns=" + ",".join(str(c) for c in columns[:8]))
    if result.get("figures"):
        try:
            bits.append(f"{len(result['figures'])} figures")
        except TypeError:
            bits.append("figures")
    error = result.get("error")
    if error:
        bits.append("error=" + str(error)[:160])
    return "; ".join(bits)[:360] if bits else f"{tool_name} completed"


def _record_tool_checkpoint(
    *,
    chat_session_id: str | None,
    python_session_id: str,
    tool_call: dict,
    result: Any,
) -> dict[str, Any] | None:
    session_id = _checkpoint_session_id(chat_session_id, python_session_id)
    if not session_id:
        return None
    try:
        from app.services import workflow_checkpoint

        tool_name = str(tool_call.get("name") or "")
        step = workflow_checkpoint.record_step(
            session_id,
            tool_name,
            _hash_tool_input(tool_call.get("input")),
            _checkpoint_status(result),
            _checkpoint_cache_refs(tool_name, result, python_session_id),
            error=(str(result.get("error"))[:500] if isinstance(result, dict) and result.get("error") else None),
            tool_call_id=str(tool_call.get("id") or "") or None,
            summary=_checkpoint_result_summary(tool_name, result),
        )
        return {
            "session_id": session_id,
            "step_idx": step.step_idx,
            "tool_name": step.tool_name,
            "status": step.status,
            "cache_refs": step.cache_refs,
            "summary": step.summary,
        }
    except Exception:
        logger.debug("workflow checkpoint write failed", exc_info=True)
        return None


def _format_checkpoint_resume_note(session_id: str | None) -> str:
    if not session_id:
        return ""
    try:
        from app.services import workflow_checkpoint

        summary = workflow_checkpoint.summarize(session_id)
    except Exception:
        return ""
    if not summary.get("has_checkpoint"):
        return ""
    steps = summary.get("steps", [])[-8:]
    lines = [
        "[RUNTIME CHECKPOINT: previous tool steps exist for this chat/session.",
        "Use cached results before rerunning expensive archive queries. Relevant cache refs include latest_adql, latest_adql_set, latest_sdss_sql, latest_lightcurve, and python_session state.",
    ]
    for step in steps:
        bits = [
            f"#{step.get('step_idx')} {step.get('tool_name')} {step.get('status')}",
            f"refs={step.get('cache_refs') or []}",
        ]
        if step.get("summary"):
            bits.append(str(step.get("summary")))
        lines.append("- " + "; ".join(bits))
    lines.append("If the workflow budget is nearly exhausted, summarize these checkpoints and ask the user to continue from them.]")
    return "\n".join(lines)


def _filter_tools(tool_names: list[str] | None, tools: list[dict]) -> list[dict]:
    if not tool_names:
        return tools
    allowed = set(tool_names)
    selected = [tool for tool in tools if tool["name"] in allowed]
    return selected or tools


def _is_tool_inventory_request(message: str) -> bool:
    """Detect prompts whose goal is to inspect the actual callable tool schema."""
    msg = (message or "").lower()
    zh_markers = ("工具清单", "工具列表", "有哪些工具", "可用工具", "工具 schema", "工具名")
    en_markers = (
        "tool list",
        "available tools",
        "which tools",
        "what tools",
        "tool schema",
        "function schema",
        "registered tools",
    )
    return any(marker in msg for marker in zh_markers + en_markers)


def _trim_large_tool_results(messages: list[dict]) -> list[dict]:
    """G6.2: shrink oversized tool_result / assistant content so the full
    message array stays under Anthropic's ~200 KB prompt cap.

    Rule: any single content block whose JSON serialization exceeds 30 KB
    is replaced with a summary stub {shape, preview, note}.  Preserves the
    structural role/content shape so the downstream LLM still sees a valid
    tool_result — just much smaller.
    """
    import json as _json

    PER_BLOCK_MAX = 30_000

    def _shrink_string(s: str) -> str:
        if len(s) <= PER_BLOCK_MAX:
            return s
        return (
            s[:8000]
            + f"\n...[TRIMMED by pre-flight: {len(s) - 8000} chars dropped; "
            + f"original was {len(s)} chars. This tool_result was from a "
            + "previous turn. If you need to re-read it, ask the user to "
            + "re-run the tool or start a new chat.]"
        )

    out: list[dict] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            out.append({**m, "content": _shrink_string(content)})
            continue
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_result":
                        raw = block.get("content")
                        if isinstance(raw, str) and len(raw) > PER_BLOCK_MAX:
                            new_blocks.append({**block, "content": _shrink_string(raw)})
                            continue
                    block_str = _json.dumps(block, default=str)
                    if len(block_str) > PER_BLOCK_MAX:
                        new_blocks.append({
                            "type": block.get("type", "text"),
                            "text": (
                                f"[TRIMMED content block, original size "
                                f"{len(block_str)} bytes; see session history for full data]"
                            ),
                        })
                        continue
                new_blocks.append(block)
            out.append({**m, "content": new_blocks})
            continue
        out.append(m)
    return out


async def _build_runtime(
    req: ChatRequest,
    user: User | None,
    db: AsyncSession,
):
    from app.services.ai_tools import TOOLS

    normalized_messages = _normalize_messages(req.messages)
    safe_context = _safe_context(req.context)
    system = SYSTEM_PROMPT
    system += "\n\nIMPORTANT: After completing the user's request, always suggest 2-3 concrete next steps the user could take. Format them as a brief list at the end of your response."
    if safe_context:
        ctx_str = json.dumps(safe_context, indent=2, default=str)[:2000]
        system += f"\n\nCurrent user context:\n{ctx_str}"
    if user:
        username = getattr(user, "username", None) or user.email.split("@")[0]
        system += f"\nUser username: {username}, Subscription: {user.subscription_tier}"

    latest_user_message = next(
        (message.content for message in reversed(req.messages) if message.role == "user"),
        "",
    )
    toolset = TOOLS
    agent_names = ["orchestrator"]
    user_context = ""
    merged_system = system
    try:
        runtime = await orchestrator.build_runtime_context(
            latest_user_message,
            normalized_messages,
            user.id if user else None,
            db,
        )
        runtime_prompt = str(runtime.get("system_prompt", "") or "").strip()
        if runtime_prompt:
            merged_system += "\n\n" + runtime_prompt
        toolset = TOOLS if _is_tool_inventory_request(latest_user_message) else _filter_tools(runtime.get("tool_names"), TOOLS)
        if runtime.get("agent_names"):
            agent_names = list(runtime["agent_names"])
        user_context = str(runtime.get("user_context", "") or "")
    except Exception as exc:
        logger.warning("Falling back to default orchestrator context: %s", exc)

    return {
        "base_system": system,
        "system": merged_system,
        "toolset": toolset,
        "agent_names": agent_names,
        "user_context": user_context,
    }


def _parse_actions(text: str) -> list[dict]:
    """Extract action JSON from <actions>...</actions> tags."""
    actions = []
    import re

    matches = re.findall(r"<actions>(.*?)</actions>", text, re.DOTALL)
    for match in matches:
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, list):
                actions.extend(parsed)
            else:
                actions.append(parsed)
        except json.JSONDecodeError:
            # Try line-by-line
            for line in match.strip().split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        actions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return actions


@router.post("/message/stream")
@limiter.limit("15/minute")
async def chat_message_stream(
    request: Request,
    req: ChatRequest,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Streaming version — sends SSE events as AI processes tools."""
    from starlette.responses import StreamingResponse

    async def generate():
        # ══════════════════════════════════════════════════════════════
        # B-R1 FIX (post-regression): flush SSE preamble + initial status
        # BEFORE any heavy setup.  Previously _build_runtime (orchestrator
        # intent classification + DB lookup + possibly ADS fetch) ran
        # first, taking 30-60 s on complex prompts.  Render / Cloudflare
        # free-tier idle-close the connection before our first yield,
        # and the client sees "response stream closed before any content was received".
        # First-byte latency must stay < 5 s.
        # ══════════════════════════════════════════════════════════════
        import json as _json
        import asyncio as _aio
        import time as _time_mod

        _stream_t0 = _time_mod.monotonic()
        debug_stream = _debug_stream_enabled(request, req.context)
        workflow_budget = _workflow_budget_config(_infer_workflow_budget_mode(req))

        def _debug_frame(stage: str, **extra: Any) -> str:
            if not debug_stream:
                return ""
            payload = {
                "type": "stream_debug",
                "stage": stage,
                "elapsed_ms": int((_time_mod.monotonic() - _stream_t0) * 1000),
                **extra,
            }
            return f"data: {_json.dumps(payload, default=str)}\n\n"

        # Frame 1: 8 KB padding SSE comment — forces edge proxies to
        # flush immediately, before any slow backend work.
        yield ": " + (" " * SSE_PREAMBLE_PADDING_BYTES) + "\n\n"
        # Frame 2: status so the UI shows "Thinking..." right away.
        yield f"data: {_json.dumps({'type': 'status', 'message': 'Thinking...'})}\n\n"
        if debug_stream:
            yield _debug_frame(
                "stream_open",
                workflow_budget_mode=workflow_budget["mode"],
                endpoint_timeout_seconds=int(workflow_budget["endpoint_timeout_seconds"]),
            )
        if workflow_budget["mode"] == "long":
            yield (
                "data: "
                + _json.dumps({
                    "type": "status",
                    "message": (
                        f"Long workflow budget enabled ({int(workflow_budget['agent_loop_seconds'])}s). "
                        "Intermediate results will be checkpointed."
                    ),
                })
                + "\n\n"
            )

        # Reuse the same logic but yield intermediate results
        provider_api_keys = _provider_api_keys(req.context, user)
        preferred_backend = _preferred_backend(req.context)
        preferred_model_profile = _preferred_model_profile(req.context)

        claude_messages: list[dict] = _normalize_messages(req.messages)

        # G6.2 / G6.3: payload pre-flight.  The reviewer reported the chat
        # endpoint returning "payload likely rejected before the app server
        # handled it" — a symptom of the previous turn's giant tool_result
        # being forwarded verbatim into this request.  Trim oversized tool
        # result blocks (>30 KB each) down to a shape+size+preview summary
        # so the LLM's context stays under Anthropic's 200 KB cap.
        claude_messages = _trim_large_tool_results(claude_messages)

        # G6.3 v2 (post-audit): earlier version hard-rejected the request at
        # 180 KB, which blocked legitimate long chats entirely.  New posture:
        # drop oldest message pairs until under cap, keeping at least the
        # last 4 turns (8 messages) + the current user query.  Only bail if
        # EVEN after aggressive trimming a single message is still > 180 KB
        # (essentially impossible after _trim_large_tool_results ran).
        CAP_BYTES = 180_000
        KEEP_TAIL_MIN = 9  # current user + up to 4 prior turns (4 × 2)

        def _payload_size(msgs: list[dict]) -> int:
            return sum(len(_json.dumps(m, default=str)) for m in msgs)

        trimmed_rounds = 0
        while _payload_size(claude_messages) > CAP_BYTES and len(claude_messages) > KEEP_TAIL_MIN:
            # Drop the oldest two messages (one turn: user + assistant)
            claude_messages = claude_messages[2:]
            trimmed_rounds += 1

        if trimmed_rounds > 0:
            # Prepend a synthetic system-style note so the model knows it
            # lost context rather than pretending the conversation started
            # fresh.  Role 'user' with a brief framing is accepted by both
            # Anthropic and OpenAI schemas.
            claude_messages.insert(0, {
                "role": "user",
                "content": (
                    f"[SYSTEM NOTE — context pre-flight dropped {trimmed_rounds} "
                    f"older turn(s) to stay under the prompt size cap. The earlier "
                    f"conversation covered topics leading up to this point. If you "
                    f"need detail from a dropped turn, ask the user to restate it "
                    f"or call a tool to re-fetch.]"
                ),
            })

        # Final guard: if a SINGLE message is still over the cap (e.g.
        # user pasted a huge blob), there's nothing more we can safely
        # do — return the structured error only in this edge case.
        total_bytes = _payload_size(claude_messages)
        if total_bytes > CAP_BYTES:
            yield (
                "data: "
                + _json.dumps({
                    "type": "error",
                    "message": (
                        f"Your current message is too large even after trimming "
                        f"older context ({total_bytes} bytes, cap {CAP_BYTES}). "
                        "Shorten the message you just typed, or paste big data "
                        "into a file and use load_fits/load_csv instead."
                    ),
                    "error_class": "payload_too_large",
                })
                + "\n\n"
            )
            return

        # B-R1 FIX: run _build_runtime concurrently with a heartbeat task
        # that emits a keepalive SSE comment every 8 s.  On a slow
        # orchestrator call (ADS lookup, user-memory DB scan, etc.) the
        # connection stays warm and the client keeps seeing bytes.
        _build_task = _aio.create_task(_build_runtime(req, user, db))
        _heartbeat_start = _time_mod.monotonic()
        while not _build_task.done():
            try:
                await _aio.wait_for(_aio.shield(_build_task), timeout=8.0)
            except _aio.TimeoutError:
                # Still running → emit keepalive + another Thinking
                # status so the UI timeline doesn't look frozen.
                elapsed = int(_time_mod.monotonic() - _heartbeat_start)
                yield ": heartbeat " + str(elapsed) + "s\n\n"
                yield f"data: {_json.dumps({'type': 'status', 'message': f'Setting up (elapsed {elapsed}s)...'})}\n\n"
        try:
            runtime = _build_task.result()
            if debug_stream:
                yield _debug_frame(
                    "runtime_ready",
                    agent_names=runtime.get("agent_names"),
                    tool_count=len(runtime.get("toolset") or []),
                )
        except Exception as setup_exc:
            logger.exception("Early chat stream setup failed before agent loop")
            msg = str(setup_exc) or setup_exc.__class__.__name__
            yield (
                "data: "
                + _json.dumps({
                    "type": "error",
                    "message": msg,
                    "error_class": "stream_setup_failed",
                })
                + "\n\n"
            )
            yield f"data: {_json.dumps({'type': 'done'})}\n\n"
            return
        agent_names = list(runtime.get("agent_names") or ["orchestrator"])

        python_session_id = (req.context or {}).get("python_session_id", "default")
        chat_session_id = (req.context or {}).get("current_session_id")
        _prime_adql_context_cache(req.context, python_session_id)

        # U1 (PART U): session-history replay can take 30-60 s in long sessions
        # with historical code that performs network calls or heavy imports (e.g.
        # lightkurve warm-up). Without a heartbeat during this period Cloudflare /
        # Render closes the SSE stream on idle timeout, seen as "response stream
        # closed before any content was received". Guard with an 8 s poll + status events.
        _prime_task = _aio.create_task(
            _prime_python_session_from_history(req.messages, python_session_id)
        )
        _prime_start = _time_mod.monotonic()
        while not _prime_task.done():
            try:
                await _aio.wait_for(_aio.shield(_prime_task), timeout=8.0)
            except _aio.TimeoutError:
                elapsed = int(_time_mod.monotonic() - _prime_start)
                yield ": heartbeat prime " + str(elapsed) + "s\n\n"
                yield (
                    f"data: {_json.dumps({'type': 'status', 'message': f'Replaying session history ({elapsed}s)...'})}"
                    "\n\n"
                )
        # Surface any exception from the prime task (rare but possible —
        # a bad history cell should not silently mask the real root cause).
        try:
            _prime_task.result()
            if debug_stream:
                yield _debug_frame("python_history_replayed")
        except Exception as _prime_err:
            logger.warning("session-history prime raised: %s", _prime_err)
            if debug_stream:
                yield _debug_frame("python_history_replay_failed", error=str(_prime_err)[:300])

        # P1.3.a (2026-05-22): publish "this session is running an agent loop"
        # so the frontend (or a second tab) can tell apart 'idle' from
        # 'mid-flight' before deciding whether to reconnect or start fresh.
        # The status flip is best-effort — failure here must not block chat.
        _agent_run_id = uuid.uuid4().hex[:16]
        _run_failed = False
        work_task: asyncio.Task | None = None
        await _update_chat_session_status(
            chat_session_id, status="running", current_run_id=_agent_run_id
        )

        try:
            if len(agent_names) > 1:
                yield f"data: {json.dumps({'type': 'status', 'message': f'Routing across {len(agent_names)} specialist agents...'})}\n\n"
            for agent_name in agent_names:
                yield f"data: {json.dumps({'type': 'status', 'message': f'{agent_name} working...'})}\n\n"

            # Thinking-process streaming: an asyncio.Queue bridges
            # intermediate events from _run_agent_loop up to the SSE stream.
            # The agent pushes `agent_text`, `tool_call`, and `tool_result`
            # events; we drain the queue here and serialise them to SSE.
            # Heartbeats still fire when the queue is quiet so Render /
            # Cloudflare free-tier idle timers (~30-100s) can't kill the
            # connection.
            event_queue: asyncio.Queue[dict] = asyncio.Queue()
            # R7: capture a compact audit trail of every thinking-stream
            # event so we can persist it to ChatSession.audit_log.  Raw
            # tool_result payloads may be huge; we store a shallow preview
            # in the audit log, keeping the full result in the actions list.
            audit_trail: list[dict] = []

            async def _emit(evt: dict) -> None:
                # R7 — persist a capped copy of the event for post-hoc audit.
                try:
                    audit_entry = dict(evt)
                    if audit_entry.get("type") == "tool_result":
                        raw_preview = json.dumps(audit_entry.get("result"), default=str)
                        if len(raw_preview) > 2000:
                            audit_entry["result"] = {
                                "__preview__": True,
                                "preview": raw_preview[:2000],
                                "size": len(raw_preview),
                            }
                    audit_entry["ts"] = datetime.now(timezone.utc).isoformat()
                    audit_trail.append(audit_entry)
                    if len(audit_trail) > 500:  # bounded per-turn
                        audit_trail[:] = audit_trail[-500:]
                except Exception:
                    pass
                # Truncate tool_result payloads that could bloat the SSE
                # frame — the full (truncated) JSON is already delivered
                # to the model through the normal tool_result_blocks path.
                #
                # R8-OPEN-4 / Round 11 root cause: the old 8 KB truncation replaced
                # the **entire** tool_result with {__preview__, preview, size}, so
                # the frontend received only a string preview and lost all key
                # diagnostic fields (error / error_class / stderr / traceback /
                # success / exit_code / backend / duration_ms). The UI fell back to
                # the "subprocess crashed" placeholder. When a final tool_result was
                # rejected upstream as payload-too-large, there was no diagnostic
                # information at all. Now we preserve diagnostic fields unchanged and
                # only replace the large-volume fields (rows / data / figures /
                # variables / stdout) with preview/offloaded markers.
                if evt.get("type") == "tool_result":
                    try:
                        raw = json.dumps(evt.get("result"), default=str)
                        if len(raw) > 8000 and isinstance(evt.get("result"), dict):
                            src = dict(evt["result"])
                            # Diagnostic keys that must always be preserved (even when total size exceeds 8 KB).
                            _KEEP = {
                                "success", "error", "error_class",
                                "stderr", "stderr_note", "traceback",
                                "exit_code", "backend", "duration_ms",
                                "mode", "auto_escalated_mode", "note",
                                "analysis_status", "data_origin",
                                "__tool_status__", "__do_not_claim__",
                                "__message_to_model__",
                                "row_count", "columns", "meta",
                            }
                            slim = {k: src[k] for k in _KEEP if k in src}
                            # Research-mode cards are intentionally user-visible:
                            # plan/matrix/evidence previews must keep the fields
                            # their frontend panels need, even when the complete
                            # tool payload is too large for a live SSE frame.
                            # The final consolidated event may arrive later with
                            # the full result, but local CLI/browser streams should
                            # still show useful numbers immediately.
                            status = str(src.get("analysis_status") or "")
                            if status == "RESEARCH_PLAN_READY" and isinstance(src.get("research_plan"), dict):
                                slim["research_plan"] = src["research_plan"]
                                for key in ("plan_id", "dataset_count", "matrix_size", "publication_ready"):
                                    if key in src:
                                        slim[key] = src[key]
                            if status.startswith("RESEARCH_MATRIX") and isinstance(src.get("matrix"), list):
                                compact_cells = []
                                for cell in src.get("matrix", [])[:10]:
                                    if not isinstance(cell, dict):
                                        continue
                                    result_obj = cell.get("result") if isinstance(cell.get("result"), dict) else {}
                                    params = result_obj.get("parameters") if isinstance(result_obj, dict) and isinstance(result_obj.get("parameters"), dict) else {}
                                    diagnostics = (
                                        result_obj.get("chain_diagnostics")
                                        if isinstance(result_obj, dict) and isinstance(result_obj.get("chain_diagnostics"), dict)
                                        else {}
                                    )
                                    compact_cells.append({
                                        "label": cell.get("label"),
                                        "model": cell.get("model"),
                                        "dataset_keys": cell.get("dataset_keys"),
                                        "publication_ready": cell.get("publication_ready"),
                                        "runnable": cell.get("runnable"),
                                        "execution_level": cell.get("execution_level"),
                                        "warnings": cell.get("warnings"),
                                        "result": {
                                            "publication_ready": result_obj.get("publication_ready") if isinstance(result_obj, dict) else None,
                                            "parameters": {
                                                name: params.get(name)
                                                for name in ("H0", "omegam", "sigma8", "S8", "rd", "H0_rd")
                                                if isinstance(params, dict) and name in params
                                            },
                                            "chain_diagnostics": diagnostics,
                                            "datasets_not_run": result_obj.get("datasets_not_run") if isinstance(result_obj, dict) else None,
                                        },
                                    })
                                slim["matrix"] = compact_cells
                                for key in ("matrix_size", "ready_cells", "publication_ready", "claim_scope"):
                                    if key in src:
                                        slim[key] = src[key]
                            if status == "EVIDENCE_GRAPH_READY" and isinstance(src.get("evidence_graph"), dict):
                                graph = src["evidence_graph"]
                                slim["evidence_graph"] = {
                                    "claimable_parameters": graph.get("claimable_parameters"),
                                    "supported_claims": graph.get("supported_claims"),
                                    "unsupported_claims": graph.get("unsupported_claims"),
                                }
                                if "claimable_parameters" in src:
                                    slim["claimable_parameters"] = src["claimable_parameters"]
                            # Large-volume fields: rows / data / figures / variables /
                            # stdout are replaced with a marker + first 2000-char preview.
                            for big_key in (
                                "rows", "data", "figures",
                                "variables", "variable_types", "stdout",
                            ):
                                if big_key in src:
                                    try:
                                        v = src[big_key]
                                        if isinstance(v, (list, tuple)):
                                            slim[big_key + "__preview__"] = {
                                                "n_items": len(v),
                                                "truncated": True,
                                            }
                                        elif isinstance(v, dict):
                                            slim[big_key + "__preview__"] = {
                                                "n_keys": len(v),
                                                "truncated": True,
                                            }
                                        elif isinstance(v, str) and len(v) > 2000:
                                            slim[big_key] = v[:2000] + "…[truncated]"
                                        else:
                                            slim[big_key] = v
                                    except Exception:
                                        pass
                            slim["__preview__"] = True
                            slim["__original_size__"] = len(raw)
                            evt = dict(evt)
                            evt["result"] = slim
                    except (TypeError, ValueError):
                        pass
                await event_queue.put(evt)

            work_task = asyncio.create_task(
                asyncio.wait_for(
                    _run_orchestrated_chat(
                        runtime=runtime,
                        messages=claude_messages,
                        provider_api_keys=provider_api_keys,
                        python_session_id=python_session_id,
                        preferred_backend=preferred_backend,
                        model_profile=preferred_model_profile,
                        chat_session_id=chat_session_id,
                        on_event=_emit,
                        workflow_budget=workflow_budget,
                    ),
                    timeout=float(workflow_budget["endpoint_timeout_seconds"]),
                )
            )
            _hb_count = 0
            while not work_task.done():
                try:
                    # Drain with a 6s ceiling before the heartbeat fires.
                    # Short interval + proxy-buffer-breaking padding above
                    # means the user sees motion within seconds even when
                    # the agent is making a long LLM call.
                    evt = await asyncio.wait_for(event_queue.get(), timeout=6.0)
                    if debug_stream:
                        yield _debug_frame(
                            "sse_event",
                            event_type=evt.get("type"),
                            tool=evt.get("tool"),
                            live=evt.get("live"),
                        )
                    yield f"data: {json.dumps(evt, default=str)}\n\n"
                    _hb_count = 0
                except asyncio.TimeoutError:
                    _hb_count += 1
                    yield f"data: {json.dumps({'type': 'status', 'message': f'still thinking... ({_hb_count * 6}s)'})}\n\n"

            # Drain any events emitted after the task finished but not yet pulled.
            while not event_queue.empty():
                try:
                    evt = event_queue.get_nowait()
                    if debug_stream:
                        yield _debug_frame(
                            "sse_event",
                            event_type=evt.get("type"),
                            tool=evt.get("tool"),
                            live=evt.get("live"),
                        )
                    yield f"data: {json.dumps(evt, default=str)}\n\n"
                except asyncio.QueueEmpty:
                    break

            response = work_task.result()

            if response["reply"]:
                yield f"data: {json.dumps({'type': 'text', 'content': response['reply']})}\n\n"
            # Keep emitting the final consolidated tool_result events too —
            # downstream clients that only know the old protocol still work,
            # and the live-stream tool_result events above carry a __preview__
            # only, so the final ones deliver the full actions list.
            for action in response["actions"]:
                yield f"data: {json.dumps({'type': 'tool_result', 'tool': action.get('action'), 'result': action.get('tool_result'), 'tool_call_id': action.get('_tool_call_id')}, default=str)}\n\n"
        except (TimeoutError, asyncio.TimeoutError):
            _run_failed = True
            timeout_s = int(workflow_budget["endpoint_timeout_seconds"])
            timeout_tool_results = _tool_results_from_stream_audit(
                audit_trail if "audit_trail" in locals() else []
            )
            timeout_summary = _tool_grounded_timeout_summary(timeout_tool_results, timeout_s)
            if timeout_summary.strip():
                logger.warning(
                    "AI workflow timed out after %ss; emitting tool-grounded "
                    "timeout fallback from %d streamed tool result(s).",
                    timeout_s,
                    len(timeout_tool_results),
                )
                yield f"data: {json.dumps({'type': 'status', 'message': f'AI workflow timed out after {timeout_s}s; returning a tool-grounded partial summary.'})}\n\n"
                yield f"data: {json.dumps({'type': 'text', 'content': timeout_summary})}\n\n"
                for action in _tool_results_to_actions(timeout_tool_results):
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': action.get('action'), 'result': action.get('tool_result'), 'tool_call_id': action.get('_tool_call_id')}, default=str)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': f'AI workflow timed out after {timeout_s}s. Try a narrower query or split the task into query + analysis steps.'})}\n\n"
        except asyncio.CancelledError:
            _run_failed = True
            if work_task is not None and not work_task.done():
                work_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(work_task, timeout=2.0)
            raise
        except InferenceError as e:
            _run_failed = True
            msg = str(e) or e.__class__.__name__
            yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
        except Exception as e:
            _run_failed = True
            msg = str(e) or e.__class__.__name__
            yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
        finally:
            # P1.3.a: flip session status back even if the SSE generator is
            # cancelled by a browser close / network drop. Without this finally
            # a cancelled stream leaves ChatSession.agent_status stuck at
            # "running" forever, confusing resume/new-chat logic.
            await _update_chat_session_status(
                chat_session_id,
                status="suspended" if _run_failed else "idle",
            )

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            # Tell every known proxy not to buffer this response.  Without
            # these headers Cloudflare + Render's edge hold small SSE frames
            # until their write buffers fill, which on a quiet agent loop
            # can be minutes — looking identical to "the AI is stuck".
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/_debug/simulate_stream_failure")
async def simulate_stream_failure(request: Request):
    """Dev-only SSE failure fixture for frontend regression tests."""
    from starlette.responses import StreamingResponse

    if os.getenv("ENV") == "production" and not os.getenv("ALLOW_STREAM_DEBUG_ENDPOINT"):
        raise HTTPException(status_code=404, detail="Not found")

    async def generate():
        import json as _json

        yield ": " + (" " * SSE_PREAMBLE_PADDING_BYTES) + "\n\n"
        yield f"data: {_json.dumps({'type': 'status', 'message': 'Thinking...'})}\n\n"
        if request.query_params.get("debug_stream") == "1":
            yield f"data: {_json.dumps({'type': 'stream_debug', 'stage': 'simulated_setup_failure', 'elapsed_ms': 0})}\n\n"
        yield (
            "data: "
            + _json.dumps({
                "type": "error",
                "message": "Simulated stream setup failure",
                "error_class": "stream_setup_failed",
            })
            + "\n\n"
        )
        yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _strip_actions_from_reply(text: str) -> str:
    """Remove <actions> blocks from the user-facing reply."""
    import re

    return re.sub(r"<actions>.*?</actions>", "", text, flags=re.DOTALL).strip()


# F2.3: <tools_returned_nothing/> structured abstention parser.
# The model's entire reply is supposed to be a single self-closing XML tag
# when tools had no data.  We parse permissively: attribute order doesn't
# matter, quotes can be " or ', and whitespace may surround the tag.
_ABSTENTION_RE = __import__("re").compile(
    r"""^\s*<(?P<tag>tools_returned_nothing|toolsreturnednothing)
        (?P<attrs>[^>]*)
        /?\s*>\s*(?:</(?P=tag)>\s*)?$""",
    __import__("re").VERBOSE | __import__("re").DOTALL,
)
_ATTR_RE = __import__("re").compile(
    r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
)


# PART AC C3 — substrings whose presence in run_python code indicates
# the AI is reading REAL data from a platform cache helper or astropy /
# astroquery / lightkurve-shaped reader. The G3.2 SYNTHETIC tainter
# uses this to grant a reverse-direction exemption: "you didn't
# declare data_source as a real source, but the code body proves you
# actually read real cached data, so we won't paint the result red".
#
# Keep this in sync with the data_source contract validator's
# Mirrors the `_REAL_DATA_READERS` concept in synthetic_code_detector.py —
# both mean "the code is genuinely reading real data".
# PART AD: these names must appear as ACTUAL AST calls / imports, not as
# substrings. The old `token in code` scan could be spoofed by writing
# `get_cached_results(` inside a comment or string literal to dodge G3.2.
_REAL_CACHE_READER_CALL_NAMES = frozenset({
    "get_cached_results", "get_search_results", "get_adql_results",
    "get_adql_result_sets", "get_latest_adql_result",
    "load_fits", "load_votable", "load_csv",
    "search_lightcurve", "download_and_clean_lightcurve", "transit_search",
    "read_csv", "read_parquet",
})
_REAL_CACHE_READER_CALL_CHAINS = frozenset({
    "Table.read", "fits.open", "astropy.io.fits.open",
})
_REAL_CACHE_READER_MODULES = frozenset({"lightkurve", "astroquery"})


def _run_python_code_reads_real_cache(code: str) -> bool:
    """PART AD (hardened PART AC C3): True when run_python code REALLY reads
    real data via an actual call / import.

    Reverse-direction exemption for the G3.2 SYNTHETIC tainter. The old
    implementation used a substring scan, so `get_cached_results(` in a
    comment or string literal would spoof the exemption. We now parse the
    AST and require the reader to be a genuine Call / Import node.
    """
    if not isinstance(code, str) or not code:
        return False
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    def _chain(node: ast.AST) -> str:
        parts: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            chain = _chain(node.func)
            if not chain:
                continue
            if chain.rsplit(".", 1)[-1] in _REAL_CACHE_READER_CALL_NAMES:
                return True
            if chain in _REAL_CACHE_READER_CALL_CHAINS:
                return True
            if chain.split(".", 1)[0] in _REAL_CACHE_READER_MODULES and "." in chain:
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in _REAL_CACHE_READER_MODULES:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".", 1)[0] in _REAL_CACHE_READER_MODULES:
                return True
    return False


# `=` catches an unfinished equation / assignment ("H0 =" with no value);
# a finished equation never ends on the operator itself.
_TRUNCATED_TRAILING_PUNCT = {":", ",", "—", "–", "(", "[", "{", "="}
# `-` (ASCII hyphen) is intentionally NOT in this set — many sentences
# legitimately end with it (e.g. "M-class star") — and we don't want
# false-positive truncations on those. Em-dash and en-dash are kept
# because reply text ending on those is a strong mid-sentence signal.

_TRUNCATED_TRAILING_CONNECTIVES = {
    "or", "and", "the", "to", "of", "in", "on", "by", "for", "with",
    "a", "an", "as", "at", "but", "if", "is", "are", "was", "were",
    "where", "while", "from", "than", "then", "vs",
}


def _reply_looks_truncated(reply: str) -> bool:
    """PART AC C2 — text-shape detection of mid-sentence termination.

    Catches the M3 / R2.6 / R2.10 silent-truncation regression where
    the provider returns stop_reason="stop" / "end_turn" but the prose
    is obviously cut off (e.g. ends on a colon). Used as a fallback
    signal in the main truncation gate when stop_reason looks clean.

    Returns True when the reply's last meaningful character / word
    indicates an incomplete sentence. Returns False for already-clean
    endings (sentence-final punctuation, abstention tags, code fences).
    """
    if not reply:
        return False
    s = reply.rstrip()
    if not s:
        return False
    # Already an honest abstention tag — that's a complete reply by
    # design, not a truncation.
    if "<tools_returned_nothing" in s or "</tools_returned_nothing>" in s:
        return False
    # Inside an unclosed Markdown fence — let the caller see the raw
    # text rather than trigger a regen.
    if s.count("```") % 2 == 1:
        return False

    last_char = s[-1]
    if last_char in _TRUNCATED_TRAILING_PUNCT:
        return True
    # Sentence-final punctuation, closing quote/bracket, ellipsis →
    # the reply has a real ending.
    if last_char in {".", "!", "?", "…", '"', "'", "”", "’", ")", "]", "}", ";"}:
        return False

    # Trailing connective word (e.g. "...the", "...or") suggests the
    # next clause was cut. Strip Markdown emphasis / inline-code chars
    # and look at the final whitespace-delimited token.
    last_line = s.split("\n")[-1].rstrip("*_`>").strip()
    if not last_line:
        return False
    parts = last_line.split()
    if not parts:
        return False
    last_word = parts[-1].lower().strip(".,;:!?)]}\"'`*_~—–")
    if last_word in _TRUNCATED_TRAILING_CONNECTIVES:
        return True
    return False


def _parse_abstention_tag(reply: str) -> dict | None:
    """Return attrs dict if reply is a single <tools_returned_nothing/> tag,
    else None.  Tolerates a trailing newline or surrounding whitespace."""
    if not reply:
        return None
    reply_l = reply.lower()
    if "tools_returned_nothing" not in reply_l and "toolsreturnednothing" not in reply_l:
        return None
    m = _ABSTENTION_RE.match(reply.strip())
    if not m:
        return None
    attrs_raw = m.group("attrs") or ""
    attrs: dict = {}
    for match in _ATTR_RE.finditer(attrs_raw):
        key = _normalize_abstention_attr_key(match.group(1))
        val = match.group(2) if match.group(2) is not None else match.group(3) or ""
        attrs[key] = val.strip()
    return attrs


def _normalize_abstention_attr_key(key: str) -> str:
    """Normalize known malformed abstention attribute spellings.

    The prompt requires snake_case, but production traces occasionally
    contain variants such as `failedtools` or `suggestednext_step`.  Keep
    this recovery narrow so the UI can render a friendly card without
    treating arbitrary XML as valid.
    """
    compact = key.replace("-", "_").replace(" ", "_").lower()
    no_underscore = compact.replace("_", "")
    aliases = {
        "failedtools": "failed_tools",
        "failedtool": "failed_tools",
        "emptytools": "empty_tools",
        "emptytool": "empty_tools",
        "suggestednextstep": "suggested_next_step",
        "nextstep": "suggested_next_step",
        "reason": "rationale",
        "rationale": "rationale",
    }
    return aliases.get(no_underscore, compact)


def _classify_abstention_reason(all_tool_results: list[dict]) -> str:
    """Was this an empty-tools turn, a failed-tools turn, or a mix?"""
    statuses: list[str] = []
    for entry in all_tool_results or []:
        inner = entry.get("result") if isinstance(entry.get("result"), dict) else entry
        st = inner.get("__tool_status__") or inner.get("analysis_status")
        if isinstance(st, str):
            statuses.append(st.upper())
    has_empty = any(s == "EMPTY" for s in statuses)
    has_failed = any(s in ("FAILED", "UNAVAILABLE") for s in statuses)
    if has_empty and has_failed:
        return "mixed"
    if has_empty:
        return "empty"
    if has_failed:
        return "failed"
    return "no_tools"


def _sequence_or_mapping_is_empty(value: Any) -> bool:
    return isinstance(value, (list, tuple, dict, set)) and len(value) == 0


def _is_failed_or_empty_data_fetch(result: Any) -> bool:
    """Return True when a data-fetch result has no citeable payload.

    This intentionally includes soft failures such as timeouts and retry
    budget exhaustion.  They should not disable the tool immediately, but
    they must suppress later synthetic Python substitutions in the same
    turn.
    """
    if not isinstance(result, dict):
        return False
    status_tokens: list[str] = []
    for key in ("analysis_status", "__tool_status__", "status", "data_origin"):
        value = result.get(key)
        if isinstance(value, str):
            status_tokens.append(value.strip().upper())

    err_str = str(result.get("error") or "").lower()
    err_class = str(result.get("error_class") or "").lower()
    message_to_model = str(result.get("__message_to_model__") or "").lower()

    if any(s in {"EMPTY", "FAILED", "UNAVAILABLE"} for s in status_tokens):
        return True
    if result.get("success") is False or bool(result.get("error")):
        return True
    if result.get("row_count") == 0 or result.get("found") == 0:
        return True
    if "timeout" in err_str or "timed out" in err_str or "timeout" in err_class:
        return True
    if "retry budget" in err_str or "retry budget" in message_to_model:
        return True

    for key in ("data", "results", "rows"):
        if key in result and _sequence_or_mapping_is_empty(result.get(key)):
            return True
    return False


def _user_requested_synthetic_demo(messages: list[dict] | None) -> bool:
    text_parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
    text = " ".join(text_parts).lower()
    return any(
        keyword in text
        for keyword in (
            "demonstrate",
            "demo",
            "example",
            "synthetic",
            "mock",
            "toy model",
            "show me how",
            "tutorial",
            "演示",
            "示例",
        )
    )


def _render_abstention_card(attrs: dict, reason: str) -> str:
    """F2.3: canonical Markdown card rendered from the abstention tag.
    The model does NOT write this prose — we do, so we control the
    quality and tone.
    """
    failed = (attrs.get("failed_tools") or "").strip()
    empty = (attrs.get("empty_tools") or "").strip()
    rationale = (attrs.get("rationale") or "").strip()
    next_step = (attrs.get("suggested_next_step") or "").strip()

    header_map = {
        "empty": "✓ Honest reply — tools returned no data",
        "failed": "✓ Honest reply — tools failed to run",
        "mixed": "✓ Honest reply — tools returned no data and some failed",
        "no_tools": "✓ Honest reply — no claims to make",
    }
    header = header_map.get(reason, header_map["no_tools"])

    lines = [f"**{header}**", ""]
    if failed:
        lines.append(f"**Failed tools:** `{failed}`")
    if empty:
        lines.append(f"**Empty tools:** `{empty}`")
    if failed or empty:
        lines.append("")
    if rationale:
        lines.append(f"_{rationale}_")
        lines.append("")
    if next_step:
        lines.append(f"**Suggested next step:** {next_step}")
    if not rationale and not next_step:
        lines.append(
            "No numerical claims are made because no tool produced data "
            "this turn.  Please rephrase your question, provide target "
            "values explicitly, or try the suggested next step above."
        )
    return "\n".join(lines)


def _prime_adql_context_cache(context: dict | None, python_session_id: str) -> None:
    if not isinstance(context, dict):
        return
    from app.services.ai_tools import build_adql_result_set, replace_adql_result_sets, store_adql_result_set

    last_adql_result_sets = context.get("last_adql_result_sets")
    if isinstance(last_adql_result_sets, list) and last_adql_result_sets:
        replace_adql_result_sets(python_session_id, [item for item in last_adql_result_sets if isinstance(item, dict)])
        return

    last_adql_rows = context.get("last_adql_rows")
    last_adql = context.get("last_adql")
    if not isinstance(last_adql_rows, list) or not last_adql_rows:
        return

    if isinstance(last_adql, dict):
        service = str(last_adql.get("service") or "gaia")
        query = str(last_adql.get("query") or "")
        row_count = int(last_adql.get("row_count") or len(last_adql_rows))
        columns = [
            str(col)
            for col in (last_adql.get("columns") or [])
            if isinstance(col, str)
        ]
    else:
        service = "gaia"
        query = ""
        row_count = len(last_adql_rows)
        columns = []

    if not columns and isinstance(last_adql_rows[0], dict):
        columns = [str(col) for col in last_adql_rows[0].keys()]

    data = {
        col: [row.get(col) if isinstance(row, dict) else None for row in last_adql_rows]
        for col in columns
    }
    result_set = build_adql_result_set(
        service=service,
        query=query,
        columns=columns,
        data=data,
        row_count=row_count,
        limit=len(last_adql_rows),
    )
    store_adql_result_set(python_session_id, result_set)


def _extract_successful_python_history(messages: list[ChatMessage]) -> list[str]:
    code_blocks: list[str] = []
    for message in messages:
        if message.role != "assistant" or not message.actions:
            continue
        for action in message.actions:
            if not isinstance(action, dict):
                continue
            if action.get("action") != "run_python":
                continue
            tool_result = action.get("tool_result")
            if isinstance(tool_result, dict) and tool_result.get("success") is False:
                continue
            tool_input = action.get("tool_input") or action.get("params")
            if not isinstance(tool_input, dict):
                continue
            code = tool_input.get("code")
            if isinstance(code, str) and code.strip():
                code_blocks.append(code)
    return code_blocks


async def _prime_python_session_from_history(messages: list[ChatMessage], python_session_id: str) -> None:
    if not python_session_id or python_session_id == "default":
        return
    code_blocks = _extract_successful_python_history(messages)
    if not code_blocks:
        return

    from app.services.code_executor import replay_session_history

    maybe = replay_session_history(python_session_id, code_blocks)
    if inspect.isawaitable(maybe):
        await maybe


async def _llm_messages_create(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    provider_api_keys: dict[str, str],
    agent_name: str = "orchestrator",
    preferred_backend: str | None = None,
    model_profile: ModelProfile | None = None,
):
    """Route one model turn through the inference router."""
    # G7.3: if debug-prompt capture is on, snapshot the system + first
    # messages for the /api/chat/_debug_last_prompt endpoint.
    if os.getenv("DEBUG_LAST_PROMPT", "").strip():
        from datetime import datetime as _dt
        try:
            _LAST_PROMPT_DEBUG.update({
                "enabled": True,
                "system": system,
                "message_count": len(messages),
                "first_messages_preview": [
                    {
                        "role": m.get("role"),
                        "content_preview": (
                            str(m.get("content"))[:500]
                            if not isinstance(m.get("content"), list)
                            else f"[{len(m.get('content', []))} content blocks]"
                        ),
                    }
                    for m in messages[:3]
                ],
                "timestamp": _dt.utcnow().isoformat() + "Z",
                "agent": agent_name,
                "tools_count": len(tools),
                "tool_names": [t.get("name") for t in tools],
            })
        except Exception:
            pass
    return await inference_router.route(
        agent_name,
        messages,
        system=system,
        tools=tools,
        provider_api_keys=provider_api_keys,
        preferred_backend=preferred_backend,
        model_profile=model_profile,
        # PART AF C4 — raised from 4096 to 8192. M5 audit caught a
        # complex chat round (5 search_literature + extract + fit + 4
        # run_python with multi-panel plots) hitting the truncation gate
        # twice, losing 5 of 6 figures including the Bayesian fit band
        # and Redshift Dependence Test. 8192 is supported by Claude
        # 4.6 / 4.7 / OpenAI / DeepSeek thinking-mode and matches the
        # `max_completion_tokens` ceiling for the responses API path.
        # Cost trade-off: per-turn output cost can up to 2× on long
        # turns, but a successfully-finished long turn is more valuable
        # than a half-truncated cheap one.
        max_tokens=8192,
        temperature=0.0,
        # Timeout budget (R0c tightens single-LLM cap after R0b added
        # per-tool deadlines):
        #   outer endpoint 420s -> agent loop 360s -> single LLM call 90s
        #   -> per-tool 45s.
        # Two back-to-back 90s LLM rounds + tools still fit in 360s, and a
        # 90s single-call cap means a hung LLM can't silently eat half the
        # loop budget the way the old 150s cap did.
        backend_timeout=90.0,
    )


async def _execute_tool_calls(
    tool_calls: list[dict], api_key: str, provider_api_keys: dict[str, str], python_session_id: str,
    user_id: str | None = None, chat_session_id: str | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    loop_deadline: float | None = None,
    summary_reserve_s: float = 60.0,
    workflow_budget_mode: str = "default",
    tool_deadline_scale: float = 1.0,
) -> list[dict]:
    """Execute one model turn's tool calls concurrently while preserving order.

    Uses return_exceptions=True (H2) so that a single raising tool does not
    abort the whole turn; raised exceptions are converted into error-shaped
    result dicts that flow through normalize_tool_result downstream.

    If `on_event` is provided, each tool also gets a per-tool progress
    heartbeat (`status: running <tool>... (Ns)`) every 6s while it executes.
    Without this heartbeat a slow single tool looks indistinguishable from
    "the whole agent is stuck" in the UI.
    """
    import time as _time
    from app.services.ai_tools import execute_tool

    # E0.1: per-tool hard deadline.  A flat 45 s cap is too tight for
    # compute-heavy analysis tools — the NGC 752 reviewer saw
    # fit_isochrone time out at 45 s and the AI fell back to a biased
    # estimator (age 3.65 Gyr vs literature 1.4-1.9 Gyr).  The table
    # below gives compute-heavy tools a realistic budget while keeping
    # the default 45 s for fast search/metadata calls so one slow
    # connector still can't burn the whole 360 s agent-loop.
    _TOOL_DEADLINE_TABLE: dict[str, float] = {
        "fit_isochrone": 180.0,
        "fit_transit_model": 120.0,
        "transit_search_bls": 120.0,
        "estimate_photo_z_pro": 90.0,
        "gp_detrend_lightcurve": 90.0,
        "x_ray_spectral_fit": 90.0,
        "fit_rv_orbit": 120.0,
        "fit_sersic_morphology": 90.0,
        "analyze_spectrum_pro": 90.0,
        "compute_galaxy_sfr": 60.0,
        # G5: run_python ceiling is the `slow` mode budget + a little
        # slack. The inner `_exec_run_python` picks the real per-call
        # timeout based on the AI's declared mode.
        "run_python": 310.0,
        # Audit-2026-04-20: the 8 tools below were falling to the 45 s
        # default but do legitimately slow work (multi-TAP fan-out, LLM
        # calls, deep cross-matches).  Reviewer-style queries were
        # hitting false timeouts.  Rough envelope per tool:
        "query_gaia_cluster": 90.0,   # Gaia TAP cone + agg stats
        "get_object_dossier": 120.0,  # 6-way parallel TAP (dominant tool for "tell me about X")
        "crossmatch_catalogs": 120.0, # dual TAP + join
        "sensitivity_analysis": 120.0,# parameter sweep via run_python
        "generate_paper_draft": 180.0,# LLM call for full paper sections
        "research_workflow": 240.0,   # multi-step hypothesis test
        "full_research_report": 300.0,# validation + paper + exports
        "solve_astrometry": 90.0,     # astrometry.net can be slow
        # Note: get_extinction stays at the 45 s default — SFD lookup is
        # <1 s typical, the 3-D fallback is local analytic.
        # J2 (2026-04-20 3rd regression): run_adql previously relied on the 45 s
        # default, but integration.py's execute_adql_query switches large queries
        # (TOP>5000 / cone>1 deg / JOIN) to launch_job_async with a 300 s async
        # budget. The 45 s tool-layer deadline cuts in first and the async path
        # never runs to completion. Give run_adql 300 s to align with integration.
        # The outer agent loop total is 360 s, so one run_adql taking 300 s leaves
        # 60 s for the subsequent LLM summary — sufficient.
        "run_adql": 300.0,
        # J3: run_sdss_sql hits SDSS SkyServer with an internal httpx timeout of 120 s.
        # A bit of extra slack handles large JOINs + JSON parsing; same tier as crossmatch_catalogs.
        "run_sdss_sql": 180.0,
        # MW v_esc / halo-star workflows need a focused Gaia DR3 helper
        # rather than repeated broad source-table scans.
        "query_high_velocity_stars": 240.0,
        # MAST / lightkurve cold starts are often >45s on Render.  Keep the
        # default mode bounded, then stretch it explicitly in long mode below.
        "search_lightcurve": 90.0,
        # ── M0 Commit 4 (2026-05-18): solar_system tool deadline adjustments ──
        # Horizons cold-start on Render free tier is often >45 s; give it 90 s.
        # MPC/SBDB/Sentry have a 30 s single-HTTP-call limit; add retry/parse
        # slack to give 60 s. DAMIT is a slow host; 60 s.
        # Formula/classification tools (HG/Afro/NEATM/Opik/Bus-DeMeo/Carvano) are instant; 45 s default is enough.
        "fetch_horizons_ephemeris": 90.0,
        "query_mpc_orbit": 60.0,
        "query_sbdb_orbit": 60.0,
        "query_sbdb_close_approaches": 60.0,
        "query_sentry_risk": 60.0,
        "query_damit_shape_model": 60.0,
        # ── M0 2026-05-20: exoplanet tool deadlines ──
        # TESS lightcurve download via MAST can take 60-90 s for new sectors;
        # NEA queries usually under 30 s but give 60 s slack.
        "fetch_tess_lightcurve": 120.0,
        "query_exoplanet_archive": 60.0,
        "query_confirmed_planets": 60.0,
        "query_tess_target_list": 60.0,
    }
    _TOOL_DEADLINE_DEFAULT = 45.0

    async def _run_one(tc: dict) -> dict:
        tool_name = tc.get("name") or ""
        base_deadline_s = _TOOL_DEADLINE_TABLE.get(tool_name, _TOOL_DEADLINE_DEFAULT)
        if workflow_budget_mode == "long":
            if tool_name == "run_adql":
                base_deadline_s = max(base_deadline_s, 780.0)
            elif tool_name == "run_sdss_sql":
                base_deadline_s = max(base_deadline_s, 300.0)
            elif tool_name == "query_high_velocity_stars":
                base_deadline_s = max(base_deadline_s, 420.0)
            elif tool_name == "search_lightcurve":
                base_deadline_s = max(base_deadline_s, 240.0)
            else:
                base_deadline_s = min(base_deadline_s * max(1.0, tool_deadline_scale), 360.0)
        deadline_s = base_deadline_s
        deadline_adjusted = False
        workflow_seconds_remaining: int | None = None
        if loop_deadline is not None:
            now = _time.monotonic()
            workflow_seconds_remaining = max(0, int(loop_deadline - now))
            tool_window_s = loop_deadline - now - summary_reserve_s
            # R5: do not start a new long tool call when the agent-loop deadline
            # is near. Otherwise the final 60 s summary budget gets consumed and
            # the outer 420 s hard wall kills the entire turn. Return a regular
            # tool-shaped failure so the next LLM iteration summarizes what was
            # already gathered.
            if tool_window_s < 8.0:
                return {
                    "error": (
                        f"Tool {tool_name} was not started because the workflow "
                        f"has only {workflow_seconds_remaining}s left; summarize "
                        "the successful tool results already gathered or ask the "
                        "user to split query + analysis steps."
                    ),
                    "success": False,
                    "error_class": "workflow_deadline_near",
                    "deadline_seconds": 0,
                    "base_deadline_seconds": int(base_deadline_s),
                    "workflow_seconds_remaining": workflow_seconds_remaining,
                    "summary_reserve_seconds": int(summary_reserve_s),
                }
            deadline_s = min(base_deadline_s, tool_window_s)
            deadline_adjusted = deadline_s < base_deadline_s

        async def _emit_tool_progress(progress: dict) -> None:
            if on_event is None:
                return
            try:
                await on_event({
                    "type": "tool_progress",
                    "tool": tool_name,
                    **progress,
                })
            except Exception:
                logger.debug("tool_progress event failed", exc_info=True)

        tool_input = dict(tc.get("input") or {})
        if workflow_budget_mode == "long":
            tool_input.setdefault("_workflow_budget_mode", "long")
            if tool_name in {"run_adql", "run_sdss_sql", "query_high_velocity_stars"}:
                tool_input.setdefault("extended_timeout", True)

        task = asyncio.create_task(execute_tool(
            tool_name, tool_input, api_key, provider_api_keys, python_session_id,
            user_id=user_id, chat_session_id=chat_session_id,
            progress_callback=_emit_tool_progress,
        ))
        start = _time.monotonic()
        while not task.done():
            elapsed = _time.monotonic() - start
            if elapsed > deadline_s:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                return {
                    "error": (
                        f"Tool {tool_name} exceeded the {int(deadline_s)}s per-tool "
                        f"deadline and was cancelled. Retry with narrower parameters."
                    ),
                    "success": False,
                    "error_class": "tool_timeout",
                    "deadline_seconds": int(deadline_s),
                    "base_deadline_seconds": int(base_deadline_s),
                    "workflow_seconds_remaining": workflow_seconds_remaining,
                    "deadline_adjusted_for_workflow": deadline_adjusted,
                }
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=6.0)
            except asyncio.TimeoutError:
                if on_event is not None:
                    try:
                        await on_event({
                            "type": "status",
                            "tool": tool_name,
                            "message": f"running {tc['name']}… ({int(elapsed) + 6}s)",
                        })
                    except Exception:
                        pass
        return task.result()

    raw_results = await asyncio.gather(
        *(_run_one(tc) for tc in tool_calls),
        return_exceptions=True,
    )
    executed = []
    for tc, result in zip(tool_calls, raw_results):
        if isinstance(result, BaseException):
            logger.warning("Tool %s raised: %s", tc.get("name"), result)
            result = {
                "error": f"Tool {tc.get('name', '?')} raised {type(result).__name__}: {result}",
                "success": False,
            }
        executed.append({
            "id": tc["id"],
            "name": tc["name"],
            "input": tc["input"],
            "result": result,
        })
    return executed


def _tool_results_to_actions(all_tool_results: list[dict]) -> list[dict]:
    """Convert internal tool-result records into frontend action card structures."""
    actions: list[dict] = []
    for tr in all_tool_results:
        result = tr.get("result")
        if isinstance(result, dict) and result.get("__internal_suppressed__"):
            continue
        action = {
            "action": tr.get("tool"),
            "tool_input": tr.get("input"),
            "tool_result": result,
            "_auto_executed": True,
        }
        if tr.get("id"):
            action["_tool_call_id"] = tr.get("id")
        actions.append(action)
    return actions


def _tool_results_from_stream_audit(audit_trail: list[dict]) -> list[dict]:
    """Recover already-streamed tool results after an outer workflow timeout.

    The SSE endpoint wraps the full agent loop in ``asyncio.wait_for``.  When
    that outer timeout fires, the inner agent task is cancelled before it can
    return its normal consolidated ``actions`` list.  The thinking stream,
    however, has already emitted useful ``tool_result`` events to the browser
    and to ``audit_trail``.  Reconstruct a minimal tool-result list from that
    trail so timeout paths can still emit a deterministic, evidence-grounded
    summary instead of a blank/error-only assistant turn.

    This is intentionally generic: it does not special-case paper IDs,
    datasets, or prompts. It only reuses tool outputs that appeared in the
    current turn.
    """
    recovered: list[dict] = []
    seen_ids: set[str] = set()
    for evt in audit_trail or []:
        if not isinstance(evt, dict) or evt.get("type") != "tool_result":
            continue
        tool = str(evt.get("tool") or evt.get("name") or "").strip()
        if not tool:
            continue
        call_id = str(evt.get("tool_call_id") or evt.get("id") or "")
        dedupe_key = call_id or f"{tool}:{len(recovered)}"
        if dedupe_key in seen_ids:
            continue
        seen_ids.add(dedupe_key)
        recovered.append({
            "id": call_id or None,
            "tool": tool,
            "input": evt.get("input") if isinstance(evt.get("input"), dict) else {},
            "result": evt.get("result"),
        })
    return recovered


def _tool_grounded_timeout_summary(tool_results: list[dict], timeout_s: int) -> str:
    """Build a safe user-facing summary when the workflow budget is exhausted."""
    grounded = (
        _research_tool_grounded_summary(tool_results)
        or _line_lfr_tool_grounded_summary(tool_results)
        or _statistics_tool_grounded_summary(tool_results)
        or _cosmology_tool_grounded_summary(tool_results)
        or ""
    )
    if grounded.strip():
        return (
            "The workflow reached its time budget before the final language "
            "answer could be completed. The tools below did run in this turn, "
            "so I am returning a deterministic tool-grounded partial summary; "
            "no unsupported conclusion is being added.\n\n"
            + grounded
        )
    if tool_results:
        tool_names = ", ".join({
            str(tr.get("tool") or tr.get("name") or "unknown")
            for tr in tool_results
            if isinstance(tr, dict)
        })
        return (
            f"The workflow reached its {timeout_s}s time budget after running "
            f"these tools: {tool_names}. The tool cards are the source of "
            "truth for this partial run; no scientific conclusion is claimed "
            "from the timeout path."
        )
    return ""


def _format_dataset_gap_item(item: Any) -> str:
    """Return a user-facing name for a missing/config-only dataset entry."""
    if isinstance(item, dict):
        key = str(item.get("key") or "").strip()
        display = str(item.get("display_name") or item.get("service_name") or "").strip()
        if display and key:
            return f"{display} ({key})"
        if display:
            return display
        if key:
            return key
        return "unnamed dataset"
    return str(item)


def _line_measurement_count_from_result(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    explicit = result.get("line_measurement_count")
    if isinstance(explicit, int):
        return max(0, explicit)
    rows = result.get("line_measurements")
    if isinstance(rows, list):
        return len(rows)
    summary = result.get("llm_summary")
    if isinstance(summary, dict) and isinstance(summary.get("line_measurement_count"), int):
        return max(0, int(summary["line_measurement_count"]))
    return 0


def _line_fit_publication_ready_from_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("__tool_status__") or "").strip().upper()
    if (
        result.get("publication_ready") is False
        or result.get("__do_not_claim__") is True
        or status in {"PARTIAL", "EMPTY", "FAILED", "UNAVAILABLE", "SYNTHETIC"}
    ):
        return False
    if result.get("publication_ready") is True:
        return True
    n_used = result.get("n_used") or result.get("n_rows") or result.get("n_fit")
    if isinstance(n_used, int) and n_used >= 5 and result.get("success") is True:
        return True
    if (
        isinstance(n_used, int)
        and n_used >= 5
        and result.get("alpha") is not None
        and result.get("beta") is not None
        and (
            result.get("intrinsic_scatter_dex") is not None
            or result.get("sigma_int") is not None
        )
    ):
        return True
    return False


def _line_fit_partial_from_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if _line_fit_publication_ready_from_result(result):
        return False
    status = str(result.get("__tool_status__") or "").strip().upper()
    n_used = result.get("n_used") or result.get("n_rows") or result.get("n_fit")
    has_stats = (
        result.get("alpha") is not None
        and result.get("beta") is not None
        and (
            result.get("intrinsic_scatter_dex") is not None
            or result.get("sigma_int") is not None
        )
    )
    return bool(
        result.get("success") is True
        and isinstance(n_used, int)
        and n_used >= 5
        and has_stats
        and (
            status == "PARTIAL"
            or result.get("__do_not_claim__") is True
            or result.get("publication_ready") is False
        )
    )


def _fmt_tool_number(value: Any, digits: int = 4) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}g}"
    return "not reported"


def _line_lfr_tool_grounded_summary(tool_results: list[dict]) -> str | None:
    """Deterministic user-facing summary when the LLM returns empty prose."""
    fit: dict[str, Any] | None = None
    for entry in reversed(tool_results or []):
        if entry.get("tool") != "fit_line_lfr":
            continue
        result = entry.get("result")
        if _line_fit_publication_ready_from_result(result):
            fit = result
            break
    if not fit:
        return None

    citation_summary = fit.get("citation_summary")
    citations: list[str] = []
    table_labels: list[str] = []
    if isinstance(citation_summary, dict):
        citations = [str(c) for c in citation_summary.get("citations") or [] if c]
        table_labels = [str(t) for t in citation_summary.get("table_labels") or [] if t]

    scatter = fit.get("intrinsic_scatter_dex")
    if scatter is None:
        scatter = fit.get("sigma_int")
    scatter_hdi = fit.get("intrinsic_scatter_dex_hdi")
    scatter_text = _fmt_tool_number(scatter)
    if isinstance(scatter_hdi, list) and len(scatter_hdi) >= 2:
        scatter_text += (
            f" dex (94% HDI {_fmt_tool_number(scatter_hdi[0])}"
            f"-{_fmt_tool_number(scatter_hdi[1])})"
        )
    else:
        scatter_text += " dex"

    subsample_note = ""
    subs = fit.get("subsample_significance_test")
    if isinstance(subs, dict) and isinstance(subs.get("subsamples"), list):
        parts = []
        for item in subs["subsamples"]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "subsample")
            n = item.get("n")
            beta = item.get("beta")
            if beta is None:
                reason = str(item.get("skipped_reason") or "not fitted")
                parts.append(f"{name}: n={n}, {reason}")
            else:
                parts.append(f"{name}: n={n}, beta={_fmt_tool_number(beta)}")
        if parts:
            subsample_note = " Subsample check: " + "; ".join(parts) + "."

    cosmology = str(fit.get("cosmology_used") or "not reported")
    cosmo_manifest = fit.get("cosmology_manifest")
    cosmo_cite = ""
    if isinstance(cosmo_manifest, dict) and cosmo_manifest.get("bibcode"):
        cosmo_cite = f" ({cosmo_manifest['bibcode']})"

    n_used = fit.get("n_used")
    n_available = fit.get("n_available")
    source_text = "cited measurement rows"
    if citations:
        source_text = ", ".join(citations)
        if table_labels:
            source_text += " / " + ", ".join(table_labels)

    return (
        "Tool-grounded summary: the literature-table fit completed with "
        f"publication_ready=true, so the following numbers are copied from "
        f"`fit_line_lfr`, not from memory.\n\n"
        f"- Model: `{fit.get('model') or 'log_luminosity = alpha + beta * log10(FWHM_km_s / 100)'}`.\n"
        f"- Sample: {n_used} of {n_available} rows used from {source_text}.\n"
        f"- Fit: alpha = {_fmt_tool_number(fit.get('alpha'))} +/- "
        f"{_fmt_tool_number(fit.get('alpha_stderr'))}; beta = "
        f"{_fmt_tool_number(fit.get('beta'))} +/- "
        f"{_fmt_tool_number(fit.get('beta_stderr'))}; intrinsic scatter = "
        f"{scatter_text}.\n"
        f"- Method: {fit.get('fit_method') or 'not reported'}; "
        f"Pearson r = {_fmt_tool_number(fit.get('pearson_r'))}, "
        f"p = {_fmt_tool_number(fit.get('pearson_p'))}.\n"
        f"- Cosmology used by the tool: {cosmology}{cosmo_cite}; "
        f"cosmology_recomputed={bool(fit.get('cosmology_recomputed'))}.\n"
        f"- Lensing: demagnified sources = {fit.get('lensed_sources_demagnified')}; "
        f"lensing status unknown for {fit.get('n_lensed_unknown')} rows."
        f"{subsample_note}\n\n"
        "Scope note: this is the fit-ready table subset available this turn. "
        "It is not automatically the full multi-survey sample of the target paper "
        "unless additional extracted tables are merged into the cache."
    )


def _statistics_tool_grounded_summary(tool_results: list[dict]) -> str | None:
    """Deterministic summary for inline-array statistics when prose is empty."""
    stats: dict[str, Any] | None = None
    for entry in reversed(tool_results or []):
        if entry.get("tool") != "astro_statistics_toolbox":
            continue
        result = entry.get("result")
        if isinstance(result, dict) and result.get("success") is True:
            stats = result
            break
    if not stats:
        return None
    analysis_type = str(stats.get("analysis_type") or "")
    if analysis_type != "linear_regression":
        return None

    return (
        "Tool-grounded statistics summary: I used `astro_statistics_toolbox` "
        "on the x/y arrays supplied in this turn.\n\n"
        f"- Method: {stats.get('method') or 'not reported'}; n = {stats.get('n')}.\n"
        f"- Slope: {_fmt_tool_number(stats.get('slope'))} +/- "
        f"{_fmt_tool_number(stats.get('slope_stderr'))}.\n"
        f"- Intercept: {_fmt_tool_number(stats.get('intercept'))} +/- "
        f"{_fmt_tool_number(stats.get('intercept_stderr'))}.\n"
        f"- Residual RMS: {_fmt_tool_number(stats.get('residual_rms'))}.\n"
        f"- publication_ready={bool(stats.get('publication_ready'))}."
    )


def _cosmology_tool_grounded_summary(tool_results: list[dict]) -> str | None:
    """Deterministic cosmology summary when the LLM returns empty prose."""
    registry: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    chain: dict[str, Any] | None = None
    for entry in tool_results or []:
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        tool = entry.get("tool")
        if tool == "list_cosmology_datasets":
            registry = result
        elif tool in {"build_cosmology_likelihood", "build_cosmology_robustness_matrix"}:
            config = result
        elif tool in {
            "run_cosmology_likelihood_chain",
            "run_cosmology_robustness_matrix",
            "run_cmb_rotation_likelihood",
        }:
            chain = result

    if not any((registry, config, chain)):
        return None

    dataset_names: list[str] = []
    data_product_notes: list[str] = []
    if isinstance(registry, dict):
        for item in registry.get("datasets") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("display_name") or item.get("key") or "").strip()
            if name:
                dataset_names.append(name)
            products = item.get("data_products")
            if isinstance(products, list) and products:
                data_product_notes.append(f"{name}: {len(products)} machine-readable product(s)")

    lines = ["Tool-grounded cosmology summary:"]
    if dataset_names:
        lines.append(f"- Registry selection: {', '.join(dataset_names)}.")
    if data_product_notes:
        lines.append(f"- Data products: {'; '.join(data_product_notes)}.")

    if isinstance(config, dict):
        model = config.get("model") or config.get("model_label") or "not reported"
        config_hash = str(config.get("config_hash") or "")[:12] or "not reported"
        lines.append(
            f"- Likelihood configuration: model={model}, config_hash={config_hash}. "
            "This is configuration metadata, not a posterior result."
        )

    if isinstance(chain, dict):
        ready = chain.get("publication_ready") is True and chain.get("__do_not_claim__") is not True
        used = [
            str(item.get("display_name") or item.get("key") or "").strip()
            for item in (chain.get("datasets_used") or [])
            if isinstance(item, dict)
        ]
        not_run = [
            str(item.get("display_name") or item.get("key") or "").strip()
            for item in (chain.get("datasets_not_run") or [])
            if isinstance(item, dict)
        ]
        if ready:
            params = chain.get("parameters")
            param_parts: list[str] = []
            if isinstance(params, dict):
                for name in ("H0", "omegam", "sigma8", "S8", "beta_deg"):
                    item = params.get(name)
                    if not isinstance(item, dict):
                        continue
                    median = item.get("median")
                    hdi = item.get("hdi_94")
                    text = f"{name}={_fmt_tool_number(median)}"
                    if isinstance(hdi, list) and len(hdi) >= 2:
                        text += f" (94% HDI {_fmt_tool_number(hdi[0])}-{_fmt_tool_number(hdi[1])})"
                    param_parts.append(text)
            lines.append(
                "- Posterior status: publication-ready for the compressed-likelihood "
                "preliminary runner."
            )
            if used:
                lines.append(f"- Numerically included datasets: {', '.join(used)}.")
            if not_run:
                lines.append(
                    f"- Not included in the numerical posterior: {', '.join(not_run)}; "
                    "these still require external likelihood execution."
                )
            if param_parts:
                lines.append("- Compressed preliminary parameters: " + "; ".join(param_parts) + ".")
            lines.append(
                "Scope note: these numbers are compressed-likelihood preliminary results, "
                "not a full external Cobaya/CosmoSIS likelihood reproduction."
            )
            lines.append(
                "Do not describe these datasets as ready for full likelihood analyses "
                "unless a full external Cobaya/CosmoSIS chain has actually run."
            )
        else:
            reason = "no publication-ready compressed posterior was produced"
            warnings = chain.get("warnings")
            if isinstance(warnings, list) and warnings:
                reason = str(warnings[0])
            lines.append(f"- Posterior status: not publication-ready ({reason}).")
            if not_run:
                lines.append(
                    f"- Dataset(s) requiring external likelihood execution: {', '.join(not_run)}."
                )
            lines.append(
                "Therefore this turn can document data products and build configs, "
                "but it cannot support H0/Omega_m/S8/tension or dark-energy posterior claims."
            )

    return "\n".join(lines) if len(lines) > 1 else None


def _research_tool_grounded_summary(tool_results: list[dict]) -> str | None:
    """Deterministic Research Mode summary that avoids unsupported numbers."""
    plan: dict[str, Any] | None = None
    matrix: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    fact_check: dict[str, Any] | None = None
    for entry in tool_results or []:
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        if entry.get("tool") == "plan_research_program":
            plan = result.get("research_plan") if isinstance(result.get("research_plan"), dict) else None
        elif entry.get("tool") == "run_research_matrix":
            matrix = result
        elif entry.get("tool") == "build_evidence_graph":
            evidence = result.get("evidence_graph") if isinstance(result.get("evidence_graph"), dict) else None
        elif entry.get("tool") == "verify_research_facts":
            fact_check = result.get("fact_check_report") if isinstance(result.get("fact_check_report"), dict) else result
    if not plan and not matrix:
        return None

    ready_cells: list[str] = []
    blocked_cells: list[str] = []
    ready_cell_summaries: list[str] = []
    executed_not_ready_summaries: list[str] = []
    config_only_summaries: list[str] = []
    if isinstance(matrix, dict):
        for cell in matrix.get("matrix") or []:
            if not isinstance(cell, dict):
                continue
            label = str(cell.get("label") or "unnamed cell")
            execution_level = str(cell.get("execution_level") or "").strip()
            if cell.get("publication_ready") is True:
                ready_cells.append(label)
                result = cell.get("result") if isinstance(cell.get("result"), dict) else {}
                params = result.get("parameters") if isinstance(result, dict) and isinstance(result.get("parameters"), dict) else {}
                diagnostics = (
                    result.get("chain_diagnostics")
                    if isinstance(result, dict) and isinstance(result.get("chain_diagnostics"), dict)
                    else {}
                )
                parts = [label]
                h0 = params.get("H0") if isinstance(params, dict) and isinstance(params.get("H0"), dict) else None
                if isinstance(h0, dict) and h0.get("median") is not None:
                    parts.append(f"H0 median {_fmt_tool_number(h0.get('median'))}")
                omegam = (
                    params.get("omegam")
                    if isinstance(params, dict) and isinstance(params.get("omegam"), dict)
                    else params.get("Omega_m")
                    if isinstance(params, dict) and isinstance(params.get("Omega_m"), dict)
                    else None
                )
                if isinstance(omegam, dict) and omegam.get("median") is not None:
                    # Keep the parameter name in code formatting so Markdown
                    # does not consume the underscore and render "Omegam".
                    parts.append(f"`Omega_m` median {_fmt_tool_number(omegam.get('median'))}")
                s8 = params.get("S8") if isinstance(params, dict) and isinstance(params.get("S8"), dict) else None
                if isinstance(s8, dict) and s8.get("median") is not None:
                    parts.append(f"S8 median {_fmt_tool_number(s8.get('median'))}")
                if isinstance(diagnostics, dict):
                    ess = diagnostics.get("proposal_ess") or diagnostics.get("ess_bulk")
                    rhat = diagnostics.get("rhat")
                    if ess is not None:
                        parts.append(f"ESS {_fmt_tool_number(ess)}")
                    if rhat is not None:
                        parts.append(f"Rhat {_fmt_tool_number(rhat)}")
                ready_cell_summaries.append(" · ".join(parts))
            else:
                blocked_cells.append(label)
                result = cell.get("result") if isinstance(cell.get("result"), dict) else {}
                diagnostics = (
                    result.get("chain_diagnostics")
                    if isinstance(result, dict) and isinstance(result.get("chain_diagnostics"), dict)
                    else {}
                )
                warnings = [str(w) for w in cell.get("warnings") or [] if w]
                datasets_not_run = (
                    result.get("datasets_not_run")
                    if isinstance(result, dict) and isinstance(result.get("datasets_not_run"), list)
                    else []
                )
                if execution_level == "executed_not_ready" or diagnostics:
                    detail_parts = [label]
                    ess = diagnostics.get("proposal_ess") or diagnostics.get("ess_bulk")
                    rhat = diagnostics.get("rhat")
                    threshold = (
                        diagnostics.get("thresholds", {}).get("ess_min")
                        if isinstance(diagnostics.get("thresholds"), dict)
                        else None
                    )
                    if ess is not None:
                        if threshold is not None:
                            detail_parts.append(
                                f"ESS {_fmt_tool_number(ess)} below threshold {_fmt_tool_number(threshold)}"
                            )
                        else:
                            detail_parts.append(f"ESS {_fmt_tool_number(ess)}")
                    if rhat is not None:
                        detail_parts.append(f"Rhat {_fmt_tool_number(rhat)}")
                    detail_parts.append("not claimable")
                    executed_not_ready_summaries.append(" · ".join(detail_parts))
                elif execution_level == "config_only" or datasets_not_run:
                    reason = ""
                    if datasets_not_run:
                        gap_names = [_format_dataset_gap_item(x) for x in datasets_not_run[:3]]
                        suffix = f"; +{len(datasets_not_run) - 3} more" if len(datasets_not_run) > 3 else ""
                        reason = "missing executable dataset(s): " + ", ".join(gap_names) + suffix
                    elif warnings:
                        reason = warnings[0]
                    else:
                        reason = "configuration only, no posterior run"
                    config_only_summaries.append(f"{label} · {reason}")
                else:
                    reason = warnings[0] if warnings else "not runnable in this turn"
                    config_only_summaries.append(f"{label} · {reason}")

    gaps = [
        str(item)
        for item in (plan or {}).get("blocking_gaps", [])
        if item
    ]
    claimable = []
    if isinstance(evidence, dict):
        claimable = [str(item) for item in evidence.get("claimable_parameters") or []]

    lines = ["Research-mode summary:", ""]
    if gaps:
        lines.extend([
            "Model-level limitation (read first)",
            "- This paper-style question cannot be fully tested at its intended "
            "model level because the required runner(s) are missing: "
            + "; ".join(gaps[:3])
            + ("." if len(gaps) <= 3 else f"; +{len(gaps) - 3} more."),
            (
                "- The executable baseline below is compressed-likelihood preliminary only."
                if ready_cells
                else "- No publication-ready compressed-likelihood baseline completed this turn."
            ),
            "",
        ])
    lines.extend([
        "What can be tested now",
        "- The research plan and matrix have been built from registered datasets and controlled runners.",
    ])
    if ready_cells:
        lines.append(
            "- Runnable compressed-likelihood preliminary cells: "
            + ", ".join(ready_cells[:6])
            + ("." if len(ready_cells) <= 6 else f", +{len(ready_cells) - 6} more.")
        )
    else:
        lines.append("- No publication-ready compressed-likelihood cell completed this turn.")

    lines.extend(["", "Executed analyses"])
    if plan:
        probes = ", ".join(str(item) for item in plan.get("required_probes", []) if item)
        models = ", ".join(str(item) for item in plan.get("model_families", []) if item)
        if probes:
            lines.append(f"- Required probes identified: {probes}.")
        if models:
            lines.append(f"- Model families planned: {models}.")
    if isinstance(matrix, dict):
        lines.append(
            f"- Research matrix cells evaluated: {matrix.get('ready_cells', 0)} ready out of {matrix.get('matrix_size', 0)}."
        )

    lines.extend(["", "Preliminary findings"])
    if ready_cell_summaries:
        lines.append(
            "- Ready compressed-likelihood preliminary cells: "
            + "; ".join(ready_cell_summaries[:5])
            + ("." if len(ready_cell_summaries) <= 5 else f"; +{len(ready_cell_summaries) - 5} more.")
        )
        lines.append(
            "- These are compressed-likelihood preliminary numbers, not full external Cobaya/CosmoSIS likelihood results."
        )
    elif ready_cells:
        lines.append("- The ready cells can support compressed-likelihood preliminary interpretation.")
    else:
        lines.append("- This turn supports dataset/method availability only, not posterior claims.")

    lines.extend(["", "Robustness"])
    if executed_not_ready_summaries:
        lines.append(
            "- Executed but not claimable because diagnostics were below publication threshold: "
            + "; ".join(executed_not_ready_summaries[:5])
            + ("." if len(executed_not_ready_summaries) <= 5 else f"; +{len(executed_not_ready_summaries) - 5} more.")
        )
    if config_only_summaries:
        lines.append(
            "- Config-only or not-runnable branches: "
            + "; ".join(config_only_summaries[:5])
            + ("." if len(config_only_summaries) <= 5 else f"; +{len(config_only_summaries) - 5} more.")
        )
    if not executed_not_ready_summaries and not config_only_summaries:
        lines.append("- All planned matrix cells that were generated are runnable in the current compressed layer.")

    lines.extend(["", "What drives the result"])
    if claimable:
        lines.append(
            "- Claimable parameters in the evidence graph: "
            + ", ".join(claimable[:8])
            + ("." if len(claimable) <= 8 else f", +{len(claimable) - 8} more.")
        )
    else:
        lines.append("- No numeric claim should be made unless it appears in a publication-ready tool card.")

    if isinstance(fact_check, dict):
        lines.extend(["", "Fact verification"])
        _fc_status = str(fact_check.get("status", "unknown"))
        _rewrites = fact_check.get("safe_rewrites")
        _has_rewrites = isinstance(_rewrites, list) and bool(_rewrites)
        _verified = fact_check.get("verified_claim_count", 0)
        _unsupported = fact_check.get("unsupported_claim_count", 0)
        if _fc_status == "blocked" and _has_rewrites:
            lines.append(
                f"- Unsafe draft claims were detected and rewritten out of this "
                f"final summary — the visible answer is safe "
                f"({_verified} verified, {_unsupported} removed/rewritten)."
            )
        elif _fc_status == "blocked":
            lines.append(
                f"- Fact-check blocked the draft and no safe rewrite was available "
                f"({_unsupported} unsupported/contradicted) — treat the tool cards "
                f"as the source of truth."
            )
        else:
            lines.append(
                f"- Fact-check {_fc_status}: {_verified} verified, "
                f"{_unsupported} unsupported/contradicted."
            )

    lines.extend(["", "What is not yet supported"])
    if gaps:
        lines.extend(f"- {gap}" for gap in gaps[:5])
    elif executed_not_ready_summaries or config_only_summaries:
        if executed_not_ready_summaries:
            lines.append("- Some executed cells need stronger diagnostics before their values can be treated as results.")
        if config_only_summaries:
            lines.append("- Some requested cells still need an executable likelihood runner or registered covariance.")
    else:
        lines.append("- Full external Cobaya/CosmoSIS reproduction is still outside the compressed preliminary layer.")

    lines.extend(["", "Next experiment"])
    lines.append(
        "- Run the missing external likelihoods or add the corresponding executable runner before making stronger research claims."
    )
    return "\n".join(lines)


def _cosmology_dataset_keys_present(tool_results: list[dict]) -> set[str]:
    keys: set[str] = set()

    def collect_from(value: Any) -> None:
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and entry.get("key"):
                    keys.add(str(entry["key"]))
                elif isinstance(entry, str):
                    keys.add(entry)

    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        for field in ("datasets", "datasets_used", "datasets_not_run", "dataset_keys", "candidate_dataset_keys"):
            collect_from(result.get(field))
        matrix = result.get("matrix")
        if isinstance(matrix, list):
            for cell in matrix:
                if isinstance(cell, dict):
                    collect_from(cell.get("dataset_keys"))
                    cell_result = cell.get("result")
                    if isinstance(cell_result, dict):
                        for field in ("datasets", "datasets_used", "datasets_not_run", "dataset_keys"):
                            collect_from(cell_result.get(field))
        provenance = result.get("provenance")
        if isinstance(provenance, dict):
            likelihood = provenance.get("cosmology_likelihood")
            if isinstance(likelihood, dict):
                for field in ("dataset_keys", "datasets_used", "datasets_not_run"):
                    collect_from(likelihood.get(field))
    return keys


_COSMOLOGY_ANCHOR_NUMERIC_PATTERNS: tuple[tuple[set[str], re.Pattern[str]], ...] = (
    (
        {"planck2018_compressed"},
        re.compile(
            r"\b(?:Planck|CMB)[^\n]{0,100}(?:H_?0|H₀|S_?8|sigma_?8|σ_?8)"
            r"[^\n]{0,60}\d",
            re.I,
        ),
    ),
    (
        {"shoes_h0_riess22"},
        re.compile(
            r"\b(?:SH0ES|Riess)[^\n]{0,100}(?:H_?0|H₀)[^\n]{0,60}\d",
            re.I,
        ),
    ),
    (
        {"h0licow_h0"},
        re.compile(r"\b(?:H0LiCOW|time[-\s]?delay)[^\n]{0,100}(?:H_?0|H₀)[^\n]{0,60}\d", re.I),
    ),
    (
        {"megamaser_h0_pesce20"},
        re.compile(r"\b(?:megamaser|maser)[^\n]{0,100}(?:H_?0|H₀)[^\n]{0,60}\d", re.I),
    ),
)


def _unsupported_cosmology_anchor_numeric_comparison(
    reply: str,
    tool_results: list[dict],
) -> bool:
    """Catch H0/S8 comparison anchors that were not selected this turn."""
    text = str(reply or "")
    if not text:
        return False
    dataset_keys = _cosmology_dataset_keys_present(tool_results)
    for required_keys, pattern in _COSMOLOGY_ANCHOR_NUMERIC_PATTERNS:
        if pattern.search(text) and not (required_keys & dataset_keys):
            return True
    return False


def _sanitize_tools_returned_nothing(reply: str) -> str:
    import re

    text = str(reply or "")
    if "<tools_returned_nothing" not in text and "<toolsreturnednothing" not in text:
        return text

    failed = ""
    rationale = ""
    next_step = ""
    for attr, target in (
        ("failed_tools", "failed"),
        ("failedtools", "failed"),
        ("rationale", "rationale"),
        ("suggested_next_step", "next"),
        ("suggestednext_step", "next"),
        ("suggestednextstep", "next"),
    ):
        match = re.search(attr + r"=[\"']([^\"']*)[\"']", text, re.I)
        if not match:
            continue
        if target == "failed":
            failed = match.group(1)
        elif target == "rationale":
            rationale = match.group(1)
        elif target == "next":
            next_step = match.group(1)

    def _user_safe_detail(value: str) -> str:
        safe = str(value or "")
        replacements = {
            "extract_literature_tables": "table extraction",
            "extractliteraturetables": "table extraction",
            "search_literature": "literature search",
            "searchliterature": "literature search",
            "read_arxiv_paper": "paper reader",
            "readarxivpaper": "paper reader",
            "fit_line_lfr": "line-relation fitting",
            "fitlinelfr": "line-relation fitting",
            "line_measurements": "line measurements",
            "linemeasurements": "line measurements",
            "run_python": "analysis-code execution",
            "runpython": "analysis-code execution",
        }
        for needle, replacement in replacements.items():
            safe = re.sub(re.escape(needle), replacement, safe, flags=re.I)
        safe = re.sub(r"\bfailedtools\b", "failed steps", safe, flags=re.I)
        safe = re.sub(r"\bemptytools\b", "empty steps", safe, flags=re.I)
        safe = re.sub(r"\bsuggestednext_?step\b", "suggested next step", safe, flags=re.I)
        safe = re.sub(r"</?tools_?returned_?nothing[^>]*>", "", safe, flags=re.I)
        return re.sub(r"\s+", " ", safe).strip()

    failed = _user_safe_detail(failed)
    rationale = _user_safe_detail(rationale)
    next_step = _user_safe_detail(next_step)

    parts = [
        "I could not complete the requested analysis with the data steps that succeeded this turn."
    ]
    if rationale:
        parts.append(rationale)
    if failed:
        parts.append(f"Unavailable or failed data step(s): {failed}.")
    if next_step:
        parts.append(f"Suggested next step: {next_step}")
    parts.append(
        "I am not reporting unsupported numerical conclusions because the required tool-backed data were not available."
    )
    return "\n\n".join(parts)


def _line_fit_context(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in (
        "[cii]", "c ii", "cii", "l[cii]", "lcii", "line luminosity",
        "fwhm", "line width", "linewidth", "alma", "alpine", "rebels",
        "relation", "correlation", "regression", "fit", "fitting",
    ))


def _is_line_relation_workflow(text: str) -> bool:
    lowered = str(text or "").lower()
    has_line_quantity = any(token in lowered for token in (
        "fwhm", "line width", "linewidth", "线宽",
    ))
    has_luminosity_context = any(token in lowered for token in (
        "lfr", "[cii]", "c ii", "cii", "l[cii]", "lcii", "谱线", "光度",
    ))
    has_fit_request = any(token in lowered for token in (
        "slope", "intercept", "scatter", "regression", "relation",
        "correlation", "fit", "fitting", "拟合", "回归", "标度关系",
        "斜率", "截距", "散布",
    ))
    return has_line_quantity and has_luminosity_context and has_fit_request


def _extract_arxiv_id_from_paper(paper: dict[str, Any]) -> str:
    import re

    bibcode = str(paper.get("bibcode") or "").strip()
    match = re.search(r"arxiv[:\s/]+(\d{4}\.\d{4,5}(?:v\d+)?)", bibcode, re.I)
    if match:
        return match.group(1)

    for value in paper.values():
        if not isinstance(value, str):
            continue
        match = re.search(r"(?:arxiv[:\s/]+)?(\d{4}\.\d{4,5}(?:v\d+)?)", value, re.I)
        if match:
            return match.group(1)
    return ""


def _rank_literature_candidate_for_line_lfr(paper: dict[str, Any]) -> tuple[int, str]:
    """Score search_literature papers for table extraction in line-LFR workflows.

    This is deliberately domain-general: prefer papers likely to contain
    machine-readable line measurements, and heavily penalize obvious topical
    drift.  It does not encode any target paper's final answer.
    """
    title = str(paper.get("title") or "")
    abstract = str(paper.get("abstract") or "")
    bibcode = str(paper.get("bibcode") or "")
    source = str(paper.get("source") or "")
    haystack = " ".join([title, abstract, bibcode, source]).lower()
    score = 0

    arxiv_id = _extract_arxiv_id_from_paper(paper)
    if arxiv_id:
        score += 2

    positive_groups: tuple[tuple[tuple[str, ...], int], ...] = (
        (("[cii]", "c ii", "cii]", "158", "158um", "158 μm", "158 micron"), 6),
        (("fwhm", "line width", "linewidth", "velocity width"), 5),
        (("line luminosity", "luminosity", "l[c", "l'"), 4),
        (("data processing", "catalogs", "catalogues", "statistical source properties"), 10),
        (("catalog", "catalogue", "table", "data release"), 4),
        (("survey strategy", "sample properties", "observations and sample"), 5),
        (("survey", "source properties", "measurements", "detected galaxies"), 3),
        (("alpine", "rebels", "alma large program"), 3),
        (("relation", "correlation", "scaling", "line-flux", "line flux"), 2),
        (("redshift", "high-redshift", "high redshift"), 1),
    )
    for tokens, weight in positive_groups:
        if any(token in haystack for token in tokens):
            score += weight

    negative_groups: tuple[tuple[tuple[str, ...], int], ...] = (
        (("withdrawn", "administratively withdrawn"), 25),
        (("wildfire", "power line", "shutoff", "electric grid"), 25),
        (("nuclear mass", "hartree-bogoliubov", "drhbc"), 22),
        (("access point", "wi-fi", "wireless network"), 18),
        (("semiring", "krotov", "perverse sheaves", "proof of"), 16),
        (("weak lensing cluster mass", "dark matter 2013"), 10),
        (("luminosity function", "sfr relation", "undetected", "serendipitous"), 5),
        (("size of", "halo", "halos", "outflows", "nature of"), 4),
    )
    for tokens, penalty in negative_groups:
        if any(token in haystack for token in tokens):
            score -= penalty

    # Require at least one explicit line/far-infrared hook for extraction;
    # otherwise abstract search can drift into cosmology or generic high-z
    # papers that cannot support L[CII]-FWHM measurements.
    if not any(token in haystack for token in ("cii", "[cii]", "c ii", "158", "alma", "alpine", "rebels")):
        score -= 8

    return score, arxiv_id


def _ranked_literature_arxiv_candidates(tool_results: list[dict]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in tool_results or []:
        if entry.get("tool") != "search_literature":
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for paper in result.get("results") or []:
            if not isinstance(paper, dict):
                continue
            score, arxiv_id = _rank_literature_candidate_for_line_lfr(paper)
            if not arxiv_id or score < 6 or arxiv_id in seen:
                continue
            seen.add(arxiv_id)
            ranked.append({
                "arxiv_id": arxiv_id,
                "score": score,
                "title": str(paper.get("title") or "").strip(),
            })
    ranked.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
    return ranked[:6]


def _verified_line_relation_seed_candidates(latest_user_text: str) -> list[dict[str, Any]]:
    """Return verified table-extraction seed papers when external search is unavailable.

    This does not provide measurement values or fit results.  It only gives
    the agent a vetted arXiv ID that must still pass
    `extract_literature_tables` in the current turn.  The motivation is
    operational: arXiv/ADS searches are rate-limited and can return empty even
    though the platform has a verified [CII] source-table seed.
    """
    text = str(latest_user_text or "").lower()
    if not _line_fit_context(text):
        return []
    if not any(token in text for token in ("[cii]", "cii", "c ii", "158")):
        return []
    try:
        from app.api.admin_literature import DEFAULT_CII_ARXIV_IDS
    except Exception:
        DEFAULT_CII_ARXIV_IDS = ("2002.00962",)
    return [
        {
            "arxiv_id": arxiv_id,
            "score": 100,
            "title": "Verified [CII] line-measurement seed; must be extracted before use",
            "seed_source": "verified_cii_admin_literature",
        }
        for arxiv_id in DEFAULT_CII_ARXIV_IDS
    ]


def _literature_arxiv_candidates(tool_results: list[dict]) -> list[str]:
    return [
        str(item["arxiv_id"])
        for item in _ranked_literature_arxiv_candidates(tool_results)
        if item.get("arxiv_id")
    ]


def _table_extraction_arxiv_ids(tool_results: list[dict]) -> set[str]:
    attempted: set[str] = set()
    for entry in tool_results or []:
        if entry.get("tool") != "extract_literature_tables":
            continue
        tool_input = entry.get("input")
        if not isinstance(tool_input, dict):
            continue
        candidate = _extract_arxiv_id_from_paper(tool_input)
        if not candidate and isinstance(tool_input.get("paper"), dict):
            candidate = _extract_arxiv_id_from_paper(tool_input["paper"])
        if candidate:
            attempted.add(candidate)
    return attempted


def _run_python_reads_real_cache(code: str) -> bool:
    return any(token in str(code or "") for token in (
        "get_cached_results(", "get_search_results(", "get_adql_results(",
        "get_adql_result_sets(", "load_fits(",
    ))


_NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _parse_inline_numeric_array(text: str, name: str) -> list[float] | None:
    """Parse a small user-supplied array like x=[0,1,2] from chat text."""
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*[:=：＝]\s*[\[\(（]\s*([^\]\)）]+?)\s*[\]\)）]",
        re.I,
    )
    match = pattern.search(str(text or ""))
    if not match:
        return None
    values = [float(v) for v in re.findall(_NUMBER_RE, match.group(1))]
    return values or None


def _parse_inline_uniform_error(text: str, axis: str, n: int) -> list[float] | None:
    """Parse phrases like 'x error is 0.1' (or the Chinese equivalent) or 'x and y errors are 0.1'."""
    if n <= 0:
        return None
    lower = str(text or "").lower()
    both_axis = axis in {"x", "y"} and re.search(
        rf"(?:x\s*(?:和|and|/|,|，)\s*y|每个点.*?x.*?y|both\s+x\s+and\s+y)"
        rf".{{0,30}}(?:误差|error|uncertaint).*?(?:都是|均为|为|=|are|is)?\s*({_NUMBER_RE})",
        lower,
        re.I | re.S,
    )
    if both_axis:
        return [float(both_axis.group(1))] * n
    axis_match = re.search(
        rf"(?<![a-z0-9_]){re.escape(axis)}(?:[_\s-]*(?:err|error|uncertainty)|\s*误差)"
        rf".{{0,20}}(?:都是|均为|为|=|are|is)?\s*({_NUMBER_RE})",
        lower,
        re.I | re.S,
    )
    if axis_match:
        return [float(axis_match.group(1))] * n
    array = (
        _parse_inline_numeric_array(text, f"{axis}_err")
        or _parse_inline_numeric_array(text, f"{axis}err")
    )
    if array and len(array) == n:
        return array
    return None


def _inline_statistics_tool_call_from_prompt(text: str) -> dict[str, Any] | None:
    """Return a deterministic statistics tool call for explicit inline data."""
    prompt = str(text or "")
    x = _parse_inline_numeric_array(prompt, "x")
    y = _parse_inline_numeric_array(prompt, "y")
    if not x or not y or len(x) != len(y) or len(x) < 2:
        return None
    lowered = prompt.lower()
    regression_tokens = (
        "linear regression", "line fit", "fit a line", "regression",
        "线性回归", "拟合", "斜率", "截距",
    )
    if not any(tok in lowered for tok in regression_tokens):
        return None
    tool_input: dict[str, Any] = {
        "analysis_type": "linear_regression",
        "x": x,
        "y": y,
        "method": "auto",
    }
    x_err = _parse_inline_uniform_error(prompt, "x", len(x))
    y_err = _parse_inline_uniform_error(prompt, "y", len(y))
    if x_err:
        tool_input["x_err"] = x_err
    if y_err:
        tool_input["y_err"] = y_err
    return {
        "id": f"auto_stats_{uuid.uuid4().hex}",
        "name": "astro_statistics_toolbox",
        "input": tool_input,
    }


def _is_cosmology_likelihood_workflow(text: str) -> bool:
    prompt = str(text or "").lower()
    dataset_tokens = (
        "bao", "baryon acoustic", "sn ia", "supernova", "pantheon",
        "des-sn", "union3", "cmb", "planck", "act dr6", "spt", "spt-3g", "sh0es",
        "cosmic chronometer", "weak lensing", "weak-lensing",
        "cosmic shear", "cosmic-shear", "kids",
        "des y3", "hsc", "galaxy lensing", "trgb", "freedman",
        "h0licow", "time-delay", "time delay", "strong-lens",
        "strong lens", "megamaser", "cosmic-chronometer", "h(z)",
        "expansion-history", "expansion history", "observational-cosmology",
        "observational cosmology", "cosmology probes",
    )
    model_tokens = (
        "dark energy", "dark-energy", "暗能量", "lcdm", "λcdm", "wcdm", "w0wa",
        "cpl", "omega_m", "ωm", "Ωm", "h0", "h₀", "posterior", "后验",
        "likelihood", "协方差", "covariance", "robustness",
        "pull", "outlier", "residual", "bin-level", "分红移",
        "s8", "sigma8", "σ8", "tension", "consistency",
        "cross-check", "cross check", "workflow",
        "constraint", "constraints", "compressed product",
        "compressed products", "chain", "chains",
        "expansion-history", "expansion history", "h(z)",
        "chronometer", "chronometers",
    )
    planning_tokens = (
        "available", "可用", "dataset", "数据集", "prior", "引用",
        "compare", "比较", "constraint", "约束", "model", "模型",
        "chain", "配置", "cobaya", "cosmosis", "workflow",
        "posterior", "run", "executable", "product", "products",
        "config-only", "config only", "research", "study", "analysis",
        "robustness", "matrix", "test", "consistent", "supported",
        "summary", "summaries", "pairwise", "approximation",
        "approximations", "conclusion", "conclusions", "availability",
        "available", "研究", "分析", "稳健",
    )
    return (
        any(tok in prompt for tok in dataset_tokens)
        and any(tok in prompt for tok in model_tokens)
        and any(tok in prompt for tok in planning_tokens)
    )


def _is_research_program_workflow(text: str) -> bool:
    prompt = str(text or "").lower()
    if _cosmology_requires_dedicated_spectra_likelihood(prompt) or _cosmology_has_dedicated_model_gap(prompt):
        return True
    research_tokens = (
        "research", "study", "analysis", "analyze", "assess", "evaluate",
        "compare", "test", "inspect", "examine", "constrain", "identify",
        "blind", "workflow", "robustness", "matrix",
        "研究", "分析", "评估", "比较", "检验", "盲测", "稳健",
        "张力", "新结论", "发现",
    )
    return _is_cosmology_likelihood_workflow(text) and any(
        token in prompt for token in research_tokens
    )


def _research_plan_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(tool_results):
        if item.get("tool") != "plan_research_program":
            continue
        result = item.get("result")
        if isinstance(result, dict) and isinstance(result.get("research_plan"), dict):
            return result["research_plan"]
    return None


def _research_evidence_graph_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(tool_results):
        if item.get("tool") != "build_evidence_graph":
            continue
        result = item.get("result")
        if isinstance(result, dict) and isinstance(result.get("evidence_graph"), dict):
            return result["evidence_graph"]
    return None


def _compact_tool_results_for_evidence(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in tool_results:
        result = item.get("result")
        if isinstance(result, dict):
            result = {
                key: value
                for key, value in result.items()
                if key in {
                    "analysis_status",
                    "publication_ready",
                    "claim_scope",
                    "dataset_keys",
                    "datasets",
                    "datasets_used",
                    "datasets_not_run",
                    "parameters",
                    "posterior_summary",
                    "derived_params",
                    "pairwise_tensions",
                    "provenance",
                    "research_plan",
                    "matrix",
                    "warnings",
                }
            }
        compact.append({
            "tool": item.get("tool"),
            "input": item.get("input"),
            "result": result,
        })
    return compact


def _cosmology_prompt_mentions_act(text: str) -> bool:
    prompt = str(text or "").lower()
    return bool(re.search(r"\bact\b", prompt)) or any(tok in prompt for tok in (
        "act dr6", "act lens", "act-era", "act era",
    ))


def _cosmology_prompt_mentions_spt(text: str) -> bool:
    prompt = str(text or "").lower()
    return bool(re.search(r"\bspt\b", prompt)) or "spt-3g" in prompt


def _cosmology_prompt_forbids_family(text: str, aliases: tuple[str, ...]) -> bool:
    """Return True when the prompt explicitly excludes a probe family.

    This is intentionally conservative and local: it only looks for probe
    aliases inside a short negation span, so ordinary phrases such as
    "config-only/no posterior" do not accidentally ban a dataset family.
    """
    prompt = str(text or "").lower()
    negators = (
        "不要加入", "不要使用", "不要引入", "不加入", "不使用", "不引入",
        "别加入", "别使用", "排除", "without", "exclude",
        "do not include", "do not use", "do not add",
        "don't include", "don't use", "don't add",
        "not include", "not use", "not add",
    )
    for negator in negators:
        start = 0
        while True:
            index = prompt.find(negator, start)
            if index < 0:
                break
            window = prompt[max(0, index - 16) : index + 96]
            if "with and without" in window or "with/without" in window:
                start = index + len(negator)
                continue
            post_window = prompt[index + len(negator) : index + len(negator) + 96]
            post_window = re.split(r"[.;\n]", post_window, maxsplit=1)[0]
            if any(alias in post_window for alias in aliases):
                return True
            start = index + len(negator)
    return False


def _cosmology_forbidden_probe_families(text: str) -> set[str]:
    return {
        family
        for family, aliases in {
            "bao": ("bao", "baryon acoustic", "desi", "sdss", "boss", "eboss", "6df"),
            "sn": ("sn", "sn ia", "supernova", "pantheon", "des-sn", "union3"),
            "cmb": ("cmb", "planck", "act dr6", "act lens"),
            "wl": ("weak lensing", "weak-lensing", "cosmic shear", "kids", "des y3", "hsc"),
            "h0": (
                "sh0es", "h0 prior", "h₀ prior", "trgb", "freedman",
                "h0licow", "time-delay", "time delay", "strong-lens",
                "strong lens", "megamaser",
            ),
            "hz": ("chronometer", "h(z)", "cosmic chronometer"),
        }.items()
        if _cosmology_prompt_forbids_family(text, aliases)
    }


def _cosmology_probe_family_for_dataset(key: str) -> str:
    if key in {"desi_dr1_bao", "sdss_6df_bao"}:
        return "bao"
    if key in {"pantheon_plus", "des_sn5yr", "union3"}:
        return "sn"
    if key in {"planck2018_compressed", "act_dr6_lensing", "spt3g_cmb"}:
        return "cmb"
    if key in {"kids1000_wl", "des_y3_3x2pt", "hsc_y1_cosmic_shear"}:
        return "wl"
    if key in {
        "shoes_h0_riess22",
        "trgb_h0_freedman19",
        "h0licow_h0",
        "megamaser_h0_pesce20",
    }:
        return "h0"
    if key == "cosmic_chronometers":
        return "hz"
    return "other"


def _cosmology_prompt_mentions_bao(text: str) -> bool:
    prompt = str(text or "").lower()
    return any(tok in prompt for tok in ("bao", "baryon acoustic", "desi", "sdss", "6df", "6dfgs", "boss", "eboss"))


def _cosmology_prompt_mentions_weak_lensing(text: str) -> bool:
    prompt = str(text or "").lower()
    return any(tok in prompt for tok in (
        "weak lensing", "weak-lensing", "weak-lensing survey",
        "weak lensing survey", "galaxy lensing", "cosmic shear",
        "kids", "kilo-degree", "des y3", "des-y3", "hsc",
    ))


def _cosmology_dataset_keys_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    if _cosmology_requires_dedicated_spectra_likelihood(prompt):
        return []
    forbidden = _cosmology_forbidden_probe_families(prompt)
    keys: list[str] = []
    h0_anchor_context = any(tok in prompt for tok in (
        "h0 prior", "h₀ prior", "h0-prior", "h₀-prior",
        "h0 priors", "h₀ priors", "h0-priors", "h₀-priors",
        "h0 constraint", "h₀ constraint", "late-universe h0",
        "late universe h0", "distance ladder", "anchors",
    ))
    pre_desi_bao = any(tok in prompt for tok in (
        "pre-desi", "pre desi", "non-desi", "non desi",
        "pre-desi bao", "before desi", "rather than desi",
        "not desi",
    ))
    desi_or_pre_desi = any(tok in prompt for tok in (
        "desi or pre-desi",
        "desi or pre desi",
        "desi/pre-desi",
        "desi/pre desi",
        "desi and pre-desi",
        "desi and pre desi",
    ))
    if desi_or_pre_desi:
        keys.extend(["desi_dr1_bao", "sdss_6df_bao"])
    elif "desi" in prompt and not pre_desi_bao:
        keys.append("desi_dr1_bao")
    elif any(tok in prompt for tok in ("bao", "baryon acoustic")):
        if pre_desi_bao or any(tok in prompt for tok in ("act dr6", "act lens", "sdss", "6df", "6dfgs", "eboss", "boss")):
            keys.append("sdss_6df_bao")
        else:
            keys.append("desi_dr1_bao")
    if any(tok in prompt for tok in ("pantheon", "supernova", "sn ia", "sn")):
        keys.append("pantheon_plus")
    if any(tok in prompt for tok in ("des-sn", "des sn", "des-5yr", "des 5yr", "desy5")):
        keys.append("des_sn5yr")
    if "union3" in prompt or "unity" in prompt:
        keys.append("union3")
    if any(tok in prompt for tok in ("cmb", "planck")):
        keys.append("planck2018_compressed")
    if (
        not keys
        and _cosmology_has_dedicated_model_gap(prompt)
        and any(tok in prompt for tok in ("compressed cosmology", "public compressed", "cosmology data", "data-combination", "data combination"))
    ):
        keys.append("planck2018_compressed")
    if (
        _cosmology_prompt_mentions_act(prompt)
        or "cmb lensing" in prompt
        or "cmb-lensing" in prompt
    ):
        keys.append("act_dr6_lensing")
    if _cosmology_prompt_mentions_spt(prompt):
        keys.append("spt3g_cmb")
    specific_wl_requested = any(tok in prompt for tok in (
        "kids", "kilo-degree", "des y3", "des-y3", "dark energy survey",
        "hsc", "hyper suprime",
    ))
    if any(tok in prompt for tok in ("kids", "kilo-degree")):
        keys.append("kids1000_wl")
    if any(tok in prompt for tok in ("des y3", "des-y3", "dark energy survey", "galaxy weak lensing")):
        keys.append("des_y3_3x2pt")
    if any(tok in prompt for tok in ("hsc", "hyper suprime")):
        keys.append("hsc_y1_cosmic_shear")
    if _cosmology_prompt_mentions_weak_lensing(prompt) and not specific_wl_requested:
        for key in ("kids1000_wl", "des_y3_3x2pt", "hsc_y1_cosmic_shear"):
            keys.append(key)
    if "chronometer" in prompt or "cc" in prompt:
        keys.append("cosmic_chronometers")
    explicit_h0_prior_selected = False
    if "trgb" in prompt or "freedman" in prompt:
        keys.append("trgb_h0_freedman19")
        explicit_h0_prior_selected = True
    if any(tok in prompt for tok in ("h0licow", "time-delay", "time delay", "strong-lens", "strong lens")) or (
        h0_anchor_context
        and "lensing" in prompt
        and not _cosmology_prompt_mentions_weak_lensing(prompt)
    ):
        keys.append("h0licow_h0")
        explicit_h0_prior_selected = True
    if "megamaser" in prompt or "maser" in prompt:
        keys.append("megamaser_h0_pesce20")
        explicit_h0_prior_selected = True
    wants_shoes = "sh0es" in prompt or "riess" in prompt
    wants_shoes_alongside_specific_anchor = any(
        tok in prompt
        for tok in (
            "compare against sh0es", "compare with sh0es", "vs sh0es",
            "versus sh0es", "+ sh0es", "plus sh0es", "include sh0es",
            "combine with sh0es", "from sh0es", "sh0es,", "sh0es /",
            "sh0es and", "sh0es +", "sh0es/trgb", "sh0es, trgb",
        )
    )
    if wants_shoes and (not explicit_h0_prior_selected or wants_shoes_alongside_specific_anchor):
        keys.append("shoes_h0_riess22")
        explicit_h0_prior_selected = True
    elif (
        not explicit_h0_prior_selected
        and ("h0 prior" in prompt or "h₀ prior" in prompt)
    ):
        keys.append("shoes_h0_riess22")
    if not keys and _cosmology_likelihood_executable_only_prompt(prompt):
        keys = ["planck2018_compressed", "act_dr6_lensing", "kids1000_wl"]
    elif not keys and _is_cosmology_likelihood_workflow(text):
        keys = ["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"]
    return [
        key
        for key in dict.fromkeys(keys)
        if _cosmology_probe_family_for_dataset(key) not in forbidden
    ]


def _cosmology_supernova_sets_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    keys: list[str] = []
    if any(tok in prompt for tok in ("pantheon", "supernova", "sn ia", "sn")):
        keys.append("pantheon_plus")
    if any(tok in prompt for tok in ("des-sn", "des sn", "des-5yr", "des 5yr", "desy5")):
        keys.append("des_sn5yr")
    if "union3" in prompt or "unity" in prompt:
        keys.append("union3")
    return list(dict.fromkeys(keys))


def _cosmology_models_from_prompt(text: str) -> list[str]:
    prompt = str(text or "").lower()
    models: list[str] = []
    if "lcdm" in prompt or "λcdm" in prompt:
        models.append("lcdm")
    if "wcdm" in prompt:
        models.append("wcdm")
    if "w0wa" in prompt or "cpl" in prompt:
        models.append("w0wa_cdm")
    wants_curvature = any(tok in prompt for tok in (
        "curvature", "curved", "non-flat", "nonflat", "omega_k",
        "omegak", "Ωk", "曲率", "非平坦",
    ))
    wants_neutrino_mass = any(tok in prompt for tok in (
        "neutrino", "mnu", "m_ν", "mν", "sum m", "Σm", "Σmν",
        "nu mass", "中微子",
    ))
    if wants_curvature:
        if "w0wa_cdm" in models:
            models.append("ok_w0wa_cdm")
        elif "wcdm" in models:
            models.append("ok_wcdm")
        else:
            models.append("ok_lcdm")
    if wants_neutrino_mass:
        if "w0wa_cdm" in models:
            models.append("w0wa_cdm_mnu")
        else:
            models.append("lcdm_mnu")
    if not models and (
        _is_cosmology_likelihood_workflow(text)
        or _should_build_cosmology_robustness_matrix(text)
        or _cosmology_has_dedicated_model_gap(text)
    ):
        if any(tok in prompt for tok in (
            "dark energy", "dark-energy", "暗能量", "wcdm", "w0wa", "cpl",
            "模型比较", "model comparison", "compare model",
        )):
            models = ["lcdm", "wcdm", "w0wa_cdm"]
        else:
            models = ["lcdm"]
    return list(dict.fromkeys(models))


def _should_build_cosmology_robustness_matrix(text: str) -> bool:
    prompt = str(text or "").lower()
    sn_sets = _cosmology_supernova_sets_from_prompt(prompt)
    forbidden = _cosmology_forbidden_probe_families(prompt)
    if "bao" in forbidden:
        return False
    if not _cosmology_prompt_mentions_bao(prompt):
        return False
    robustness_tokens = (
        "robust", "鲁棒", "consistency", "一致", "compare", "比较",
        "discrepancy", "tension", "张力", "互相", "组合", "compilation",
        "des-5yr", "desy5", "union3",
    )
    return len(sn_sets) >= 2 and any(tok in prompt for tok in robustness_tokens)


def _cosmology_likelihood_build_calls_from_prompt(text: str) -> list[dict[str, Any]]:
    if _cosmology_requires_dedicated_spectra_likelihood(text):
        return []
    dataset_keys = _cosmology_dataset_keys_from_prompt(text)
    models = _cosmology_models_from_prompt(text)
    if not dataset_keys or not models:
        return []
    if _should_build_cosmology_robustness_matrix(text):
        sn_sets = _cosmology_supernova_sets_from_prompt(text)
        return [
            {
                "id": f"auto_cosmo_matrix_{uuid.uuid4().hex}",
                "name": "build_cosmology_robustness_matrix",
                "input": {
                    "model": model,
                    "supernova_sets": sn_sets,
                    "include_weak_lensing": _cosmology_prompt_mentions_weak_lensing(text),
                    "include_h0_prior": "sh0es" in str(text or "").lower()
                    or "h0 prior" in str(text or "").lower()
                    or "h₀ prior" in str(text or "").lower(),
                },
            }
            for model in models
        ]
    return [
        {
            "id": f"auto_cosmo_config_{uuid.uuid4().hex}",
            "name": "build_cosmology_likelihood",
            "input": {
                "model": model,
                "dataset_keys": dataset_keys,
                "output_format": "both",
            },
        }
        for model in models
    ]


def _cosmology_direct_route_from_prompt(text: str) -> list[dict[str, Any]] | None:
    """Detect explicit single-tool cosmology requests and return a forced
    first-iteration tool call list.

    Both DeepSeek and Anthropic's function-call rankers default to
    `plan_research_program` for any "research-flavored" user phrasing
    regardless of how specific the user is. Five rounds of prompt + schema
    work (2026-05-28 V1-V5) failed to steer them on "Hubble tension" and
    "Alcock-Paczynski" prompts. This deterministic gate fires BEFORE the
    first LLM call and pre-executes the right cosmology tool, then lets the
    LLM continue with the result already in hand — same pattern as
    `_inline_statistics_tool_call_from_prompt` and
    `_cosmology_likelihood_run_calls_from_prompt`.

    Returns ``None`` when no trigger phrase matches → normal agent loop.
    """
    raw = str(text or "")
    t = raw.lower()
    if not t:
        return None

    hubble_triggers = (
        "hubble tension",
        "compare planck and sh0es",
        "delta h0",
        "compare cosmologies",
        "luminosity-distance offset",
        "preset vs preset",
    )
    if any(k in t for k in hubble_triggers):
        return [{
            "id": f"direct_route_{uuid.uuid4().hex}",
            "name": "compare_luminosity_distances",
            "input": {"target_cosmology": "riess22_shoes"},
        }]

    ap_triggers = (
        "alcock-paczynski",
        "alcock paczynski",
        "ap test",
        "dm/dh ratio",
        "bao bin anomaly",
        "per-bin bao",
        "geometric omega_m from bao",
        "geometric ωm from bao",
    )
    if any(k in t for k in ap_triggers):
        return [{
            "id": f"direct_route_{uuid.uuid4().hex}",
            "name": "assess_bao_bin_anomaly",
            "input": {},
        }]

    return None


def _cosmology_likelihood_run_calls_from_prompt(text: str) -> list[dict[str, Any]]:
    if _cosmology_requires_dedicated_spectra_likelihood(text):
        return []
    dataset_keys = _cosmology_dataset_keys_from_prompt(text)
    models = _cosmology_models_from_prompt(text)
    if not dataset_keys or not models:
        return []
    if _cosmology_prompt_mentions_spt(text):
        # SPT-3G damping-tail likelihoods are not yet executable in the
        # registry.  Do not substitute Planck/ACT compressed posteriors for an
        # SPT workflow; the assistant should report config/registry status.
        return []
    run_models = ["lcdm"] if "lcdm" in models else [models[0]]
    if _should_build_cosmology_robustness_matrix(text):
        sn_sets = _cosmology_supernova_sets_from_prompt(text)
        return [
            {
                "id": f"auto_cosmo_run_matrix_{uuid.uuid4().hex}",
                "name": "run_cosmology_robustness_matrix",
                "input": {
                    "model": model,
                    "supernova_sets": sn_sets,
                    "include_weak_lensing": _cosmology_prompt_mentions_weak_lensing(text),
                    "include_h0_prior": "sh0es" in str(text or "").lower()
                    or "h0 prior" in str(text or "").lower()
                    or "h₀ prior" in str(text or "").lower(),
                },
            }
            for model in run_models
        ]
    return [
        {
            "id": f"auto_cosmo_run_{uuid.uuid4().hex}",
            "name": "run_cosmology_likelihood_chain",
            "input": {
                "model": model,
                "dataset_keys": dataset_keys,
            },
        }
        for model in run_models
    ]


def _cosmology_likelihood_executable_only_prompt(text: str) -> bool:
    prompt = str(text or "").lower()
    return (
        ("registry" in prompt or "registered" in prompt)
        and any(tok in prompt for tok in ("executable chain", "executable chains", "可执行"))
        and ("observational-cosmology" in prompt or "observational cosmology" in prompt or "probes" in prompt)
    )


def _cosmology_requires_dedicated_spectra_likelihood(text: str) -> bool:
    """Prompts whose observable class is spectra/parity/template level.

    Planck/ACT compressed distance or lensing summaries are useful background
    cosmology products, but they are the wrong evidence class for EB/TB
    birefringence or oscillatory primordial-feature searches.
    """
    prompt = str(text or "").lower()
    has_birefringence = any(
        tok in prompt
        for tok in (
            "birefringence",
            "polarization rotation",
            "polarization-rotation",
            "polarisation rotation",
            "polarisation-rotation",
            "rotation angle",
            "rotation-angle",
            "rotation field",
            "rotation-field",
            "eb/tb",
            "tb/eb",
            "eb tb",
            "tb eb",
            "instrument-angle",
            "instrument angle",
            "parity violation",
            "parity-violating",
            "parity violating",
            "偏振旋转",
            "旋转角",
        )
    )
    has_feature_template = any(
        tok in prompt
        for tok in (
            "primordial feature",
            "primordial-feature",
            "sharp-feature",
            "resonant-feature",
            "oscillatory feature",
            "oscillatory primordial",
            "primordial oscillation",
            "oscillatory residual",
            "feature template",
            "feature-search",
            "look-elsewhere",
            "look elsewhere",
            "frequency scan",
            "frequency/phase scan",
            "inflationary anomaly",
            "inflationary anomalies",
            "inflationary feature",
            "原初",
            "振荡",
        )
    )
    has_cmb_spectra_context = any(
        tok in prompt
        for tok in (
            "cmb",
            "planck",
            "act",
            "spt",
            "temperature",
            "polarization",
            "polarisation",
            "b-mode",
            "b mode",
            "tt",
            "te",
            "ee",
            "eb",
            "tb",
            "map",
            "maps",
            "bandpower",
            "bandpowers",
            "estimator",
            "spectra",
            "spectrum",
            "power spectrum",
            "power-spectrum",
        )
    )
    return has_cmb_spectra_context and (has_birefringence or has_feature_template)


def _cosmology_has_dedicated_model_gap(text: str) -> bool:
    prompt = str(text or "").lower()
    return any(
        tok in prompt
        for tok in (
            "early dark energy",
            "early-dark-energy",
            "early energy",
            "early-energy",
            "transient early-energy",
            "transient early energy",
            "pre-recombination",
            "before recombination",
            " ede",
            "ede ",
            "ede-vs",
            "ede vs",
            "ede claim",
            "ede model",
            "axion-like early",
            "axion like early",
            "modified gravity",
            "modified-gravity",
            "growth model",
            "growth-rate",
            "growth-index",
            "growth index",
            "growth likelihood",
            "growth than planck",
            "differs from gr",
            "thawing",
            "emergent",
            "mirage",
        )
    )


def _should_suppress_line_measurement_synthetic_python(
    tool_call: dict,
    *,
    fit_ready_cache_keys: list[str],
    latest_user_text: str,
    user_requested_synthetic_demo: bool,
) -> bool:
    if tool_call.get("name") != "run_python" or not fit_ready_cache_keys:
        return False
    if user_requested_synthetic_demo:
        return False
    tool_input = tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {}
    declared = str(tool_input.get("data_source") or "").strip()
    if declared and declared != "none_not_analyzing_real_data":
        return False
    code = str(tool_input.get("code") or "")
    if _run_python_reads_real_cache(code):
        return False
    context = "\n".join([
        latest_user_text,
        str(tool_input.get("description") or ""),
        code,
    ])
    return _line_fit_context(context)


def _suppressed_line_measurement_python_result(cache_keys: list[str]) -> dict:
    cache_key = cache_keys[-1] if cache_keys else "latest_literature_tables"
    return {
        "success": True,
        "__tool_status__": "EMPTY",
        "analysis_status": "empty",
        "data_origin": "unavailable",
        "row_count": 0,
        "__internal_suppressed__": True,
        "suppressed_reason": "fit_ready_literature_measurements_available",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"run_python was suppressed because fit-ready literature measurement "
            f"rows are already cached as {cache_key}. You MUST call "
            f"fit_line_lfr(cache_key='{cache_key}') or read cached rows with "
            "data_source='cached:<key>'. Do not create synthetic or hardcoded "
            "literature samples for this fitting task."
        ),
        "__suggested_next_step__": f"Call fit_line_lfr with cache_key={cache_key}.",
        "cache_key": cache_key,
    }


def _suppressed_line_relation_search_result() -> dict:
    return {
        "success": True,
        "__tool_status__": "EMPTY",
        "analysis_status": "empty",
        "data_origin": "unavailable",
        "row_count": 0,
        "results": [],
        "__internal_suppressed__": True,
        "__do_not_claim__": True,
        "__message_to_model__": (
            "Additional search_literature calls were suppressed because this "
            "line-luminosity/FWHM workflow already has enough abstract-level "
            "paper search results for candidate selection. Abstracts cannot "
            "support L[CII]/FWHM measurement or fit claims. Next, use "
            "extract_literature_tables on the best arXiv candidates, or "
            "honestly report that no fit-ready measurement table was found."
        ),
        "suppressed_reason": "line_relation_search_budget_exceeded",
    }


def _suppressed_line_relation_extract_result(max_attempts: int) -> dict:
    return {
        "success": True,
        "__tool_status__": "EMPTY",
        "analysis_status": "empty",
        "data_origin": "unavailable",
        "row_count": 0,
        "line_measurement_count": 0,
        "fit_ready": False,
        "__internal_suppressed__": True,
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"Additional extract_literature_tables calls were suppressed after "
            f"{max_attempts} table-extraction attempt(s) without fit-ready "
            "line_measurements. Do not keep trying broad candidates or create "
            "synthetic rows. Summarize the limitation and ask for a specific "
            "paper/table source if needed."
        ),
        "suppressed_reason": "line_relation_extract_budget_exceeded",
    }


async def _run_agent_loop(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    provider_api_keys: dict[str, str],
    agent_name: str,
    python_session_id: str,
    preferred_backend: str | None = None,
    model_profile: ModelProfile | None = None,
    user_id: str | None = None,
    chat_session_id: str | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    workflow_budget: dict[str, Any] | None = None,
) -> dict:
    """Run the agent's multi-turn loop.

    When `on_event` is provided, intermediate thinking-process events are
    emitted so the SSE endpoint can stream them to the UI in real time:
    - {"type": "agent_text", "agent": <name>, "content": <str>}  — LLM text
      produced between tool calls (the model's "thinking out loud").
    - {"type": "tool_call", "agent": <name>, "tool": <name>, "input": <dict>}
      — fires before each tool starts executing.
    - {"type": "tool_result", "agent": <name>, "tool": <name>, "result": <dict>}
      — fires when each tool completes.
    """
    # 2026-05-28: normalise public provider names to canonical backend ids.
    # The blind-test runners pass --provider anthropic / deepseek / openai,
    # but inference_router registers backends under their internal names
    # (claude vs anthropic, etc.). Without this mapping, --provider anthropic
    # silently falls through to the first available fallback backend
    # (DeepSeek when DEEPSEEK_API_KEY is also set), which is what caused
    # the V7 Opus blind run to charge zero Anthropic credit.
    if preferred_backend == "anthropic":
        preferred_backend = "claude"

    import time as _time

    async def _emit(evt: dict) -> None:
        if on_event is not None:
            try:
                await on_event(evt)
            except Exception as exc:  # never let event-pump errors kill the loop
                logger.debug("on_event failed for %s: %s", evt.get("type"), exc)

    working_messages = deepcopy(messages)
    all_tool_results: list[dict] = []
    text_parts: list[str] = []
    latest_user_text = ""
    for _msg in reversed(messages):
        if _msg.get("role") == "user":
            latest_user_text = str(_msg.get("content") or "")
            break
    skip_claim_gate_for_meta = _is_tool_inventory_request(latest_user_text)
    budget = _workflow_budget_config((workflow_budget or {}).get("mode"))
    budget.update(workflow_budget or {})
    max_iterations = int(budget.get("max_iterations", 12))
    summary_reserve_s = float(budget.get("summary_reserve_seconds", 60.0))
    soft_reminder_s = float(budget.get("soft_reminder_seconds", 75.0))
    budget_mode = str(budget.get("mode") or "default")
    _loop_seconds = float(budget.get("agent_loop_seconds", 360.0))
    # H1 / long-task bump: default remains 360s; paper-scale workflows can
    # explicitly opt into a 900s loop with larger summary reserve.
    _loop_deadline = _time.monotonic() + _loop_seconds
    checkpoint_id = _checkpoint_session_id(chat_session_id, python_session_id)
    checkpoint_note = _format_checkpoint_resume_note(checkpoint_id)

    await _emit({
        "type": "workflow_budget",
        "agent": agent_name,
        "mode": budget_mode,
        "agent_loop_seconds": int(_loop_seconds),
        "summary_reserve_seconds": int(summary_reserve_s),
        "max_iterations": max_iterations,
    })
    if checkpoint_note:
        try:
            from app.services import workflow_checkpoint
            await _emit({
                "type": "workflow_checkpoint",
                "agent": agent_name,
                "summary": workflow_checkpoint.summarize(checkpoint_id or ""),
            })
        except Exception:
            pass

    # G3.1: track which data-fetch tools have failed this turn so we can
    # suppress subsequent synthetic run_python fallbacks + physically remove
    # the failed tools from future LLM turns (G3.4).
    _DATA_FETCH_TOOLS = {
        "search_objects", "run_adql", "search_lightcurve", "query_transients",
        "crossmatch_catalogs", "query_gaia_cluster", "get_object_info",
        "get_object_dossier", "get_extinction", "search_literature",
        "extract_literature_tables", "query_high_velocity_stars",
        # solar_system M0 (2026-05-20): 6 small-body data-query tools also need
        # G3.4 circuit-breaker coverage, otherwise they can hard-fail N times
        # without ever being disabled. Confirmed by C2 case testing.
        "query_mpc_orbit", "fetch_horizons_ephemeris", "query_sbdb_orbit",
        "query_sbdb_close_approaches", "query_sentry_risk", "query_damit_shape_model",
    }
    MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS = 2
    # G3.4 + H0.7: tool → failure count this turn.  When ≥
    # DISABLE_AFTER_FAILURES, the tool is removed from the `tools`
    # parameter sent to the LLM on the next iteration — the model
    # literally cannot call it any more.
    # H0.7: raised threshold from 2 to 3, and only count RETRYABLE
    # failures (connector errors, not timeouts/payload_too_large which
    # the AI might legitimately retry with smaller scope).
    # 2026-05-20: Explicit hard-reject error_class set — these are not retryable;
    # retrying with the same parameters will be rejected again. C2 case: the LLM
    # misread range_too_large as retryable and never triggered the circuit breaker
    # after 12 attempts.
    _HARD_REJECT_ERROR_CLASSES = frozenset({
        "range_too_large",    # rejected locally by the tool, e.g. 100-year daily ephemeris
        "missing_argument",   # required parameter missing; retrying is pointless until the LLM fills it in
        "invalid_argument",   # type/range error; same as above
    })
    tool_failure_counts: dict[str, int] = {}
    # R22: row_count=0 / EMPTY are soft for retry disabling, but still mean
    # later Python cells have no real cache to analyze.  Track them separately
    # so fallback/demo code is suppressed as ∅ Empty instead of AUTO/SYNTHETIC.
    empty_data_fetches: set[str] = set()
    DISABLE_AFTER_FAILURES = 3
    synthetic_run_python_count = 0  # G3.3 counter
    user_requested_synthetic_demo = _user_requested_synthetic_demo(messages)
    fit_ready_literature_cache_keys: list[str] = []
    inline_statistics_call = _inline_statistics_tool_call_from_prompt(latest_user_text)
    research_program_workflow = _is_research_program_workflow(latest_user_text)
    cosmology_likelihood_workflow = _is_cosmology_likelihood_workflow(latest_user_text)
    cosmology_likelihood_build_calls = _cosmology_likelihood_build_calls_from_prompt(latest_user_text)
    cosmology_likelihood_run_calls = _cosmology_likelihood_run_calls_from_prompt(latest_user_text)
    # 2026-05-28: deterministic single-tool route for "Hubble tension" and
    # "Alcock-Paczynski" prompts. Fires on iteration 0 only, bypasses the
    # first LLM call. None when no trigger phrase matches.
    cosmology_direct_route_calls = _cosmology_direct_route_from_prompt(latest_user_text)

    hit_iteration_cap = False
    hit_deadline = False
    soft_deadline_reminded = False
    # C-X1 (P0): record the LLM stop_reason of the iteration that breaks
    # the agent loop so the post-loop truncation gate can detect a
    # max_tokens / length cut-off and run a safe-summary regen instead
    # of shipping a dangling partial sentence to the UI.
    last_stop_reason: str | None = None
    for _iteration in range(max_iterations):
        if _time.monotonic() > _loop_deadline:
            hit_deadline = True
            summary = " ".join(text_parts) if text_parts else "AI workflow timed out."
            return {
                "reply": (
                    summary
                    + f"\n\n(Agent loop timed out after {int(_loop_seconds)} seconds. "
                    "Results above are partial and checkpointed for continuation.)"
                ),
                "actions": _tool_results_to_actions(all_tool_results),
                "hit_deadline": True,
                "hit_iteration_cap": False,
            }

        # G3.4: filter tools that have failed too many times this turn.
        visible_tools = [
            t for t in tools
            if tool_failure_counts.get(t.get("name", ""), 0) < DISABLE_AFTER_FAILURES
        ]
        disabled_this_turn = [
            t.get("name") for t in tools
            if tool_failure_counts.get(t.get("name", ""), 0) >= DISABLE_AFTER_FAILURES
        ]
        # L1: research-focus filter (outermost). Drop tools that are not
        # in the active focus's allowlist before any later domain-specific
        # gate runs. No-op when ASTRO_RESEARCH_FOCUS=all (default).
        visible_tools = _filter_tools_by_research_focus(visible_tools)
        line_relation_workflow = _is_line_relation_workflow(latest_user_text)
        literature_searches_done = sum(
            1 for tr in all_tool_results if tr.get("tool") == "search_literature"
        )
        table_extraction_attempts_done = sum(
            1 for tr in all_tool_results if tr.get("tool") == "extract_literature_tables"
        )
        has_fit_ready_line_measurements = bool(fit_ready_literature_cache_keys) or any(
            _line_measurement_count_from_result(tr.get("result")) > 0
            for tr in all_tool_results
            if tr.get("tool") == "extract_literature_tables"
        )
        has_publication_ready_line_fit = any(
            _line_fit_publication_ready_from_result(tr.get("result"))
            for tr in all_tool_results
            if tr.get("tool") == "fit_line_lfr"
        )
        has_partial_line_fit = any(
            _line_fit_partial_from_result(tr.get("result"))
            for tr in all_tool_results
            if tr.get("tool") == "fit_line_lfr"
        )
        inline_statistics_done = any(
            tr.get("tool") == "astro_statistics_toolbox"
            for tr in all_tool_results
        )
        inline_statistics_pending = inline_statistics_call is not None and not inline_statistics_done
        research_plan_done = any(
            tr.get("tool") == "plan_research_program"
            for tr in all_tool_results
        )
        research_matrix_done = any(
            tr.get("tool") == "run_research_matrix"
            for tr in all_tool_results
        )
        research_evidence_done = any(
            tr.get("tool") == "build_evidence_graph"
            for tr in all_tool_results
        )
        research_plan_pending = research_program_workflow and not research_plan_done
        research_matrix_pending = (
            research_program_workflow
            and research_plan_done
            and cosmology_likelihood_workflow
            and not research_matrix_done
        )
        research_evidence_pending = (
            research_program_workflow
            and research_matrix_done
            and not research_evidence_done
        )
        cosmology_registry_done = any(
            tr.get("tool") == "list_cosmology_datasets"
            for tr in all_tool_results
        )
        cosmology_likelihood_config_done = any(
            tr.get("tool") in {
                "build_cosmology_likelihood",
                "build_cosmology_robustness_matrix",
            }
            for tr in all_tool_results
        )
        cosmology_likelihood_run_done = any(
            tr.get("tool") in {
                "run_cosmology_likelihood_chain",
                "run_cosmology_robustness_matrix",
            }
            for tr in all_tool_results
        )
        cosmology_registry_pending = (
            cosmology_likelihood_workflow
            and not research_program_workflow
            and not cosmology_registry_done
        )
        cosmology_likelihood_config_pending = (
            cosmology_likelihood_workflow
            and cosmology_registry_done
            and not cosmology_likelihood_config_done
            and bool(cosmology_likelihood_build_calls)
        )
        cosmology_likelihood_run_pending = (
            cosmology_likelihood_workflow
            and cosmology_likelihood_config_done
            and not cosmology_likelihood_run_done
            and bool(cosmology_likelihood_run_calls)
        )
        attempted_table_ids = _table_extraction_arxiv_ids(all_tool_results)
        ranked_arxiv_candidates = _ranked_literature_arxiv_candidates(all_tool_results)
        if line_relation_workflow:
            # Verified seeds are a resilience mechanism for ADS/arXiv drift and
            # rate limits.  Include them even when broad search returns other
            # candidates, because those candidates often contain only abstracts,
            # reviews, or non-measurement tables.  They still have to pass
            # extract_literature_tables in this turn before any value is used.
            by_arxiv_id: dict[str, dict[str, Any]] = {}
            for candidate in [
                *_verified_line_relation_seed_candidates(latest_user_text),
                *ranked_arxiv_candidates,
            ]:
                arxiv_id = str(candidate.get("arxiv_id") or "")
                if not arxiv_id:
                    continue
                prev = by_arxiv_id.get(arxiv_id)
                if prev is None or int(candidate.get("score") or 0) > int(prev.get("score") or 0):
                    by_arxiv_id[arxiv_id] = candidate
            ranked_arxiv_candidates = sorted(
                by_arxiv_id.values(),
                key=lambda item: int(item.get("score") or 0),
                reverse=True,
            )
        remaining_ranked_candidates = [
            c for c in ranked_arxiv_candidates
            if str(c.get("arxiv_id") or "") not in attempted_table_ids
        ]
        arxiv_candidates = [
            str(c["arxiv_id"])
            for c in remaining_ranked_candidates
            if c.get("arxiv_id")
        ]
        force_table_extraction = (
            line_relation_workflow
            and (
                literature_searches_done >= 2
                or table_extraction_attempts_done > 0
            )
            and table_extraction_attempts_done < MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS
            and not has_fit_ready_line_measurements
            and any(t.get("name") == "extract_literature_tables" for t in visible_tools)
            and bool(arxiv_candidates)
        )
        line_relation_extraction_exhausted = (
            line_relation_workflow
            and not has_fit_ready_line_measurements
            and table_extraction_attempts_done >= MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS
        )
        line_relation_search_exhausted = (
            line_relation_workflow
            and not has_fit_ready_line_measurements
            and literature_searches_done >= 3
            and table_extraction_attempts_done == 0
            and not arxiv_candidates
        )
        if force_table_extraction:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "search_literature",
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        elif line_relation_workflow and not has_fit_ready_line_measurements:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        if line_relation_workflow and (has_publication_ready_line_fit or has_partial_line_fit):
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "search_literature",
                    "extract_literature_tables",
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        if line_relation_extraction_exhausted or line_relation_search_exhausted:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "search_literature",
                    "extract_literature_tables",
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        if inline_statistics_pending:
            # The user supplied the numerical arrays directly. Use the
            # deterministic statistics tool as the citable path; do not let
            # the model detour through run_python and trigger synthetic output.
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "astro_statistics_toolbox"
            ]
        elif inline_statistics_call is not None and inline_statistics_done:
            visible_tools = []
        elif research_plan_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "plan_research_program"
            ]
        elif research_matrix_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "run_research_matrix"
            ]
        elif research_evidence_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "build_evidence_graph"
            ]
        elif cosmology_registry_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "list_cosmology_datasets"
            ]
        elif cosmology_likelihood_config_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") in {
                    "build_cosmology_likelihood",
                    "build_cosmology_robustness_matrix",
                }
            ]
        elif cosmology_likelihood_run_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") in {
                    "run_cosmology_likelihood_chain",
                    "run_cosmology_robustness_matrix",
                }
            ]
        elif cosmology_likelihood_workflow and cosmology_likelihood_config_done:
            visible_tools = []

        # Append a runtime note to the system message when any tools are
        # disabled this iteration, so the model understands why its previous
        # calls "disappeared" from the schema.
        if disabled_this_turn:
            system_this_call = (
                system
                + "\n\n[RUNTIME: the following tools have been removed from "
                + "your toolkit this turn because they failed "
                + f"{DISABLE_AFTER_FAILURES}+ times already: "
                + f"{disabled_this_turn}. Do NOT attempt to call them by "
                + "name (they are not in your schema). Either use a DIFFERENT "
                + "tool with DIFFERENT parameters, or emit "
                + "<tools_returned_nothing failed_tools='"
                + ",".join(disabled_this_turn) + "' ...>.]"
            )
            # G3.5: tell the frontend, via SSE, that tools have been disabled
            await _emit({
                "type": "tools_disabled",
                "agent": agent_name,
                "disabled": disabled_this_turn,
                "iteration": _iteration,
            })
        else:
            system_this_call = system

        if force_table_extraction:
            candidate_text = ", ".join(arxiv_candidates[:MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS])
            candidate_details = "; ".join(
                f"{c.get('arxiv_id')} (score {c.get('score')}: {str(c.get('title') or '')[:90]})"
                for c in remaining_ranked_candidates[:MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS]
            )
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this is a line-luminosity/FWHM relation "
                + "workflow. You have candidate papers from literature search, "
                + "verified seed metadata, or a previous failed table extraction. "
                + "Abstract-level paper results cannot support measurement or "
                + "fit claims. Do NOT call search_literature again this iteration. "
                + "Your next tool call must be extract_literature_tables for one of the "
                + f"top-ranked candidate arXiv IDs: {candidate_text}. "
                + f"Candidate details: {candidate_details}. "
                + f"Do not exceed {MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS} "
                + "total table-extraction attempts in this turn. If extraction "
                + "returns no usable line_measurements, say that clearly; "
                + "do not create synthetic or hardcoded sample rows.]"
            )

        if line_relation_extraction_exhausted:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this line-luminosity/FWHM relation workflow "
                + f"has already used {table_extraction_attempts_done} "
                + "table-extraction attempt(s) without any normalized "
                + "line_measurements. The search/extraction/fitting/Python "
                + "tools have been removed for this iteration to avoid "
                + "burning quota or inventing data. Your response must be an "
                + "honest boundary summary: literature searches found papers, "
                + "but no fit-ready measurement table was extracted this turn; "
                + "do not report slope/intercept/scatter/r/p or create a "
                + "synthetic demonstration.]"
            )

        if line_relation_search_exhausted:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this line-luminosity/FWHM relation workflow "
                + f"has already used {literature_searches_done} literature "
                + "searches without any high-confidence arXiv candidate for "
                + "machine-readable line-measurement tables. Search and "
                + "analysis tools have been removed for this iteration. "
                + "Respond with an honest boundary summary and ask for a "
                + "specific arXiv ID / table source; do not report fitted "
                + "relation statistics.]"
            )

        if inline_statistics_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: the user supplied explicit x/y arrays and "
                + "asked for a common statistical regression. The only "
                + "available tool this iteration is astro_statistics_toolbox. "
                + "Call it with the supplied arrays; do not use run_python, "
                + "do not hand-calculate, and do not make qualitative "
                + "significance claims until the statistics tool returns.]"
            )
        elif inline_statistics_call is not None and inline_statistics_done:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: astro_statistics_toolbox already returned "
                + "the regression result for the user's inline arrays. Stop "
                + "calling tools and summarize only the tool-provided slope, "
                + "intercept, method, and residual diagnostics.]"
            )
        elif research_plan_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this is a research-mode request. The only "
                + "available tool this iteration is plan_research_program. "
                + "Call it before any posterior/fit or literature summary. "
                + "The plan itself is not evidence for numerical claims.]"
            )
        elif research_matrix_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: a ResearchPlan exists for this turn. The only "
                + "available tool this iteration is run_research_matrix. Execute "
                + "the runnable compressed-likelihood cells and mark config-only "
                + "cells as not runnable. Do not invent missing likelihood results.]"
            )
        elif research_evidence_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: a research matrix has returned. The only "
                + "available tool this iteration is build_evidence_graph. Build "
                + "claim provenance for this turn before giving the final summary.]"
            )
        elif cosmology_registry_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: the user is asking about observational "
                + "cosmology datasets/likelihoods/model comparison. The only "
                + "available tool this iteration is list_cosmology_datasets. "
                + "Call it before literature search or prose so the answer is "
                + "grounded in the curated registry with versions, covariance, "
                + "units, applicability, source URLs, and citations.]"
            )
        elif cosmology_likelihood_config_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: the curated cosmology dataset registry has "
                + "already returned. The only available tools this iteration "
                + "are build_cosmology_likelihood and "
                + "build_cosmology_robustness_matrix. Build guarded configs "
                + "for the requested model/dataset combinations. These "
                + "configs are not posterior chains; do not quote posterior "
                + "constraints unless a later chain returns publication_ready=true.]"
            )
        elif cosmology_likelihood_run_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: guarded cosmology likelihood configs already "
                + "returned. The only available tools this iteration are "
                + "run_cosmology_likelihood_chain and "
                + "run_cosmology_robustness_matrix. Execute the phase-1 "
                + "compressed Gaussian likelihood where registered summaries "
                + "exist. If datasets_not_run is non-empty, say those datasets "
                + "still need external Cobaya/CosmoSIS chains; do not imply "
                + "they are included in the numerical posterior.]"
            )
        elif cosmology_likelihood_workflow and cosmology_likelihood_config_done:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: cosmology registry/config tools already "
                + "returned for this turn. Stop calling tools unless a "
                + "compressed-likelihood result is already present. Summarize "
                + "registered datasets, runnable compressed results, and which "
                + "posterior/chain claims remain unsupported.]"
            )

        if (
            cosmology_likelihood_workflow
            and _cosmology_prompt_mentions_bao(latest_user_text)
            and (
                "cmb" in _cosmology_forbidden_probe_families(latest_user_text)
                or "h0" in _cosmology_forbidden_probe_families(latest_user_text)
            )
        ):
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this is a BAO-only request without CMB "
                + "calibration and/or H0 priors. Do not introduce Planck "
                + "sound-horizon numbers, H0 values, or author-year citations "
                + "for excluded probes. State that absolute H0/rd-calibrated "
                + "claims are not determined by this tool turn.]"
            )

        if line_relation_workflow and has_publication_ready_line_fit:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: a publication-ready line-relation fit has "
                + "already succeeded this turn. Stop calling tools now. "
                + "Summarize the fitted slope/intercept/scatter with the "
                + "tool-provided citation/provenance, and explicitly mark "
                + "scope limitations such as single-survey coverage, missing "
                + "z<1 subsample, or missing lensing demagnification. Do not "
                + "start another literature-search or extraction loop.]"
            )

        if line_relation_workflow and has_partial_line_fit:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: fit_line_lfr has returned a real but PARTIAL "
                + "line-relation fit this turn. Stop calling tools now and "
                + "summarize the fit. Every sentence containing slope, "
                + "intercept/alpha, beta, intrinsic scatter, r, or p-value "
                + "MUST be introduced with exactly: "
                + "\"Exploratory only; not publication-ready:\". "
                + "Do not claim `publication_ready=true` for the overall fit; "
                + "if a nested sampler reports readiness, describe it only as "
                + "sampler convergence and keep the top-level fit partial. "
                + "Also state the scope limitations: ALPINE-only if only that "
                + "cache is present, empty z<1 split if applicable, and "
                + "missing/unknown lensing demagnification metadata.]"
            )

        if checkpoint_note:
            system_this_call = system_this_call + "\n\n" + checkpoint_note

        # PART Y Batch 5 + C-X2 (audit follow-up): synthetic_run_python_count
        # was previously incremented but never read; PART Y Batch 5 wired it
        # to a >=3 forced-abstention nudge; C-X2 tightens the threshold to
        # >=1 because the most recent audit caught a turn where the AI
        # reached for synthetic data once, hit the upstream guard ("I see
        # the system is directing me to use the cached literature data"),
        # and then kept thrashing in the cached data instead of either
        # finding another tool path or emitting <tools_returned_nothing/>.
        # Pushing for abstention as soon as the first SYNTHETIC run_python
        # appears prevents the "thrash in cached data" loop while still
        # allowing the model to recover with a DIFFERENT real-data tool.
        # We deliberately do NOT physically disable run_python (the model
        # still needs it for legitimate post-cache analysis), just push
        # hard for abstention.
        if synthetic_run_python_count >= 1:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: you have emitted "
                + f"{synthetic_run_python_count} SYNTHETIC run_python "
                + "call(s) this turn (data_source='none_not_analyzing_real_data' "
                + "or auto-tainted because upstream fetches failed). STOP "
                + "writing demo / synthetic code now. Either (a) call a "
                + "DIFFERENT real-data tool with DIFFERENT parameters, or "
                + "(b) emit <tools_returned_nothing/> with the failed_tools "
                + "list as your ENTIRE reply. Do not produce any more "
                + "SYNTHETIC output this turn.]"
            )
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "synthetic_run_python_excess_total", 1.0,
                    count=str(min(synthetic_run_python_count, 10)),
                    agent=agent_name,
                )
            except Exception:
                pass

        if fit_ready_literature_cache_keys:
            latest_cache = fit_ready_literature_cache_keys[-1]
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: extract_literature_tables has already returned "
                + f"fit-ready line_measurements cached as {latest_cache}. For any "
                + "[CII]/line-luminosity/FWHM relation, call "
                + f"fit_line_lfr(cache_key='{latest_cache}') next, or use "
                + f"run_python with data_source='cached:{latest_cache}' and "
                + "get_cached_results(). Do NOT declare "
                + "data_source='none_not_analyzing_real_data' or hardcode "
                + "ALPINE/REBELS/literature sample rows.]"
            )

        seconds_left = _loop_deadline - _time.monotonic()
        if seconds_left <= soft_reminder_s:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: you are close to the agent-loop deadline "
                + f"({max(0, int(seconds_left))}s left). Stop broad retries now. "
                + "Summarize the successful tool results already gathered, or emit "
                + "<tools_returned_nothing/> if the required data is still missing. "
                + "Do not start another broad archive query unless it is essential "
                + "and narrowly scoped.]"
            )
            if not soft_deadline_reminded:
                await _emit({
                    "type": "status",
                    "message": (
                        "Agent is near the workflow deadline; asking it to summarize "
                        "partial results instead of starting broad retries."
                    ),
                })
                soft_deadline_reminded = True

        try:
            response = await _llm_messages_create(
                system=system_this_call,
                messages=working_messages,
                tools=visible_tools,
                provider_api_keys=provider_api_keys,
                agent_name=agent_name,
                preferred_backend=preferred_backend,
                model_profile=model_profile,
            )
        except InferenceError as exc:
            # P0 (2026-05-26): tools may have completed successfully but the
            # final LLM synthesis call failed (all configured backends down).
            # Do not return an empty answer — emit a deterministic
            # tool-grounded summary built from the results already gathered.
            _synthesis_fallback = (
                _research_tool_grounded_summary(all_tool_results)
                or _line_lfr_tool_grounded_summary(all_tool_results)
                or _statistics_tool_grounded_summary(all_tool_results)
                or _cosmology_tool_grounded_summary(all_tool_results)
                or ""
            )
            if not _synthesis_fallback.strip():
                raise
            logger.warning(
                "Agent loop: synthesis backend failed (%s); emitting "
                "tool-grounded fallback. tool_results=%d",
                exc, len(all_tool_results),
            )
            text_parts.append(
                "The research tools completed, but the model's final language "
                "synthesis failed (all configured AI backends were "
                "unavailable). Below is the tool-grounded summary of what ran; "
                "no unsupported conclusion is made.\n\n" + _synthesis_fallback
            )
            break

        text = str(response.get("content", "") or "")
        tool_calls_in_turn: list[dict] = list(response.get("tool_calls") or [])
        forced_tool_call_override = False
        if inline_statistics_pending and (
            not tool_calls_in_turn
            or any(tc.get("name") != "astro_statistics_toolbox" for tc in tool_calls_in_turn)
        ):
            text = ""
            tool_calls_in_turn = [deepcopy(inline_statistics_call)]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Running the deterministic statistics toolbox on the "
                    "inline x/y arrays before summarizing the regression."
                ),
            })
        if research_plan_pending:
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_research_plan_{uuid.uuid4().hex}",
                "name": "plan_research_program",
                "input": {"question": latest_user_text},
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": "Planning the research program before running analyses.",
            })
        if research_matrix_pending:
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_research_matrix_{uuid.uuid4().hex}",
                "name": "run_research_matrix",
                "input": {
                    "research_plan": _research_plan_from_tool_results(all_tool_results),
                    "question": latest_user_text,
                },
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Executing the runnable cells of the research matrix and "
                    "marking config-only gaps."
                ),
            })
        if research_evidence_pending:
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_evidence_graph_{uuid.uuid4().hex}",
                "name": "build_evidence_graph",
                "input": {
                    "tool_results": _compact_tool_results_for_evidence(all_tool_results),
                },
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": "Building the claim provenance graph for this research turn.",
            })
        if cosmology_registry_pending:
            # Keep cosmology routing deterministic. Some backends call
            # list_cosmology_datasets without the narrowed dataset_keys input,
            # which floods the model with the entire registry and can trigger
            # memory/Python detours. The auto-selected key list is the safe
            # contract for the rest of this turn.
            registry_dataset_keys = _cosmology_dataset_keys_from_prompt(latest_user_text)
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_cosmo_registry_{uuid.uuid4().hex}",
                "name": "list_cosmology_datasets",
                "input": (
                    {"dataset_keys": registry_dataset_keys}
                    if registry_dataset_keys
                    else {}
                ),
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Listing the curated observational-cosmology dataset "
                    "registry before summarizing dataset availability."
                ),
            })
        if cosmology_likelihood_config_pending:
            text = ""
            tool_calls_in_turn = deepcopy(cosmology_likelihood_build_calls)
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Building guarded cosmology likelihood configs for the "
                    "requested model/dataset combinations."
                ),
            })
        if cosmology_likelihood_run_pending:
            text = ""
            tool_calls_in_turn = deepcopy(cosmology_likelihood_run_calls)
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Running the phase-1 compressed Gaussian cosmology "
                    "likelihood for registered executable summaries."
                ),
            })
        # 2026-05-28: cosmology direct-route early gate. Fires only on the
        # first iteration with no tool history. Bypasses both DeepSeek's and
        # Anthropic's first-call planner bias for explicit "Hubble tension"
        # / "Alcock-Paczynski" prompts. Mirrors _inline_statistics_tool_call
        # injection pattern (line 3029).
        if (
            _iteration == 0
            and not tool_calls_in_turn
            and not all_tool_results
            and cosmology_direct_route_calls
        ):
            text = ""
            tool_calls_in_turn = deepcopy(cosmology_direct_route_calls)
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    f"Direct-route trigger matched: calling "
                    f"`{cosmology_direct_route_calls[0]['name']}` "
                    "directly to bypass the planner detour."
                ),
            })
        if force_table_extraction and not tool_calls_in_turn and arxiv_candidates:
            # Some backends still choose to summarize despite the runtime
            # "next call must be extract_literature_tables" instruction.  For
            # line-relation workflows this strands a verified seed/candidate
            # and produces an honest-but-premature boundary answer.  Make the
            # routing deterministic: execute the top untried candidate, and
            # let the next iteration summarize or fit based on real results.
            text = ""
            forced_id = f"auto_extract_{uuid.uuid4().hex}"
            forced_arxiv = arxiv_candidates[0]
            tool_calls_in_turn = [{
                "id": forced_id,
                "name": "extract_literature_tables",
                "input": {"arxiv_id": forced_arxiv},
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Continuing line-relation workflow with the next ranked "
                    f"literature table candidate: arXiv:{forced_arxiv}."
                ),
            })
        if (
            line_relation_workflow
            and fit_ready_literature_cache_keys
            and not has_publication_ready_line_fit
            and not has_partial_line_fit
            and not tool_calls_in_turn
            and any(t.get("name") == "fit_line_lfr" for t in visible_tools)
        ):
            # Same principle as auto-extract above: once the platform has a
            # cited, fit-ready measurement cache, do not rely on the model to
            # remember the exact next tool call.  Execute the deterministic
            # fit path so the turn ends with a real publication-ready or
            # explicit partial fit, not a silent fallback summary.
            text = ""
            latest_cache = fit_ready_literature_cache_keys[-1]
            fit_input: dict[str, Any] = {
                "cache_key": latest_cache,
                "line_id": "[CII]",
                "fit_method_requested": _line_fit_method_from_prompt(latest_user_text),
                "variant_label": "auto_fit_from_literature_tables",
            }
            target_cosmology = _line_fit_cosmology_from_prompt(latest_user_text)
            if target_cosmology:
                fit_input["cosmology"] = target_cosmology
            subsample_splits = _line_fit_subsample_splits_from_prompt(latest_user_text)
            if subsample_splits:
                fit_input["subsample_splits"] = subsample_splits
            tool_calls_in_turn = [{
                "id": f"auto_fit_{uuid.uuid4().hex}",
                "name": "fit_line_lfr",
                "input": fit_input,
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Fitting the cached line-measurement table before summarizing "
                    "the line-luminosity/FWHM relation."
                ),
            })
        abstention_text_in_prose = (
            "<tools_returned_nothing" in text or "<toolsreturnednothing" in text
        )
        if abstention_text_in_prose:
            text = _sanitize_tools_returned_nothing(text)
        if text:
            # Research/cosmology turns can produce draft prose immediately
            # before a tool call.  That draft may contain literature priors or
            # preliminary numbers that have not passed citation/fact checks.
            # Keep the process visible, but do not expose or append unverified
            # prose when the same turn is still executing tools.
            if tool_calls_in_turn and (research_program_workflow or cosmology_likelihood_workflow):
                await _emit({
                    "type": "agent_text",
                    "agent": agent_name,
                    "content": (
                        "Draft intermediate prose withheld until the current "
                        "research-tool results pass evidence and fact checks."
                    ),
                    "draft": True,
                    "not_claimable": True,
                })
            else:
                text_parts.append(text)
                await _emit({"type": "agent_text", "agent": agent_name, "content": text})
        if tool_calls_in_turn and abstention_text_in_prose:
            break
        if not tool_calls_in_turn:
            break

        assistant_content = []
        # PART Z C6 — DeepSeek thinking-mode contract: stash the
        # reasoning_content the model produced this turn so the next
        # OpenAI-compatible request can echo it back. Anthropic/OpenAI
        # paths don't return reasoning_content; the block is dropped
        # silently on those providers via _normalize_openai_messages.
        reasoning_content = response.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            assistant_content.append({
                "type": "reasoning_content",
                "text": reasoning_content,
            })
        if text:
            assistant_content.append({"type": "text", "text": text})
        for tool_call in tool_calls_in_turn:
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                }
            )
            # Emit the tool_call event *before* dispatch so the UI can show
            # "Calling <tool>..." while the tool is still executing.
            await _emit({
                "type": "tool_call",
                "agent": agent_name,
                "tool": tool_call["name"],
                "input": tool_call["input"],
                "iteration": _iteration + 1,
                "max_iterations": max_iterations,
            })
        working_messages.append({"role": "assistant", "content": assistant_content})

        suppressed_tool_results: dict[str, dict] = {}
        real_tool_calls: list[dict] = []
        # Line-relation searches often need one broad topic query, one methods
        # query, and one narrowed survey/table query.  The previous cap of two
        # real calls could suppress the narrowed ALPINE/REBELS/arXiv query and
        # incorrectly force an honest-but-unhelpful "no usable literature"
        # answer.  Keep the anti-loop guard, but align it with the exhaustion
        # threshold below (>=3 searches with no candidates).
        search_calls_allowed_this_turn = max(0, 3 - literature_searches_done)
        search_calls_seen_this_turn = 0
        extract_calls_allowed_this_turn = max(
            0,
            MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS - table_extraction_attempts_done,
        )
        extract_calls_seen_this_turn = 0
        for tool_call in tool_calls_in_turn:
            tool_name = str(tool_call.get("name") or "")
            if (
                line_relation_workflow
                and not has_fit_ready_line_measurements
                and tool_name == "search_literature"
                and search_calls_seen_this_turn >= search_calls_allowed_this_turn
            ):
                suppressed_tool_results[tool_call["id"]] = {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                    "result": _suppressed_line_relation_search_result(),
                }
            elif (
                line_relation_workflow
                and not has_fit_ready_line_measurements
                and tool_name == "extract_literature_tables"
                and extract_calls_seen_this_turn >= extract_calls_allowed_this_turn
            ):
                suppressed_tool_results[tool_call["id"]] = {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                    "result": _suppressed_line_relation_extract_result(
                        MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS
                    ),
                }
            elif _should_suppress_line_measurement_synthetic_python(
                tool_call,
                fit_ready_cache_keys=fit_ready_literature_cache_keys,
                latest_user_text=latest_user_text,
                user_requested_synthetic_demo=user_requested_synthetic_demo,
            ):
                suppressed_tool_results[tool_call["id"]] = {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                    "result": _suppressed_line_measurement_python_result(fit_ready_literature_cache_keys),
                }
            else:
                real_tool_calls.append(tool_call)
                if line_relation_workflow and tool_name == "search_literature":
                    search_calls_seen_this_turn += 1
                if line_relation_workflow and tool_name == "extract_literature_tables":
                    extract_calls_seen_this_turn += 1

        tool_result_blocks = []
        real_executed_tools = await _execute_tool_calls(
            real_tool_calls,
            provider_api_keys.get("anthropic", ""),
            provider_api_keys,
            python_session_id,
            user_id=user_id,
            chat_session_id=chat_session_id,
            on_event=on_event,
            loop_deadline=_loop_deadline,
            summary_reserve_s=summary_reserve_s,
            workflow_budget_mode=budget_mode,
            tool_deadline_scale=float(budget.get("tool_deadline_scale", 1.0)),
        ) if real_tool_calls else []
        real_by_id = {tc["id"]: tc for tc in real_executed_tools}
        executed_tools = [
            suppressed_tool_results.get(tc["id"]) or real_by_id[tc["id"]]
            for tc in tool_calls_in_turn
        ]
        for tc in executed_tools:
            result = tc["result"]
            tool_name = tc.get("name", "")

            if tool_name == "extract_literature_tables" and isinstance(result, dict):
                measurement_count = _line_measurement_count_from_result(result)
                if measurement_count > 0:
                    cache_key = str(result.get("cache_key") or "latest_literature_tables")
                    if cache_key not in fit_ready_literature_cache_keys:
                        fit_ready_literature_cache_keys.append(cache_key)

            # G3.1 + H0.7: mark data-fetch failures for the G3.4 disable gate.
            # H0.7: timeouts / payload_too_large / row_count=0 are "soft"
            # failures — the AI can legitimately retry with smaller TOP or
            # narrower cone.  Only count connector errors (real upstream
            # failure that's not the AI's fault) toward the disable counter.
            if tool_name in _DATA_FETCH_TOOLS and isinstance(result, dict):
                status_tokens: list[str] = []
                for key in ("analysis_status", "__tool_status__", "status"):
                    v = result.get(key)
                    if isinstance(v, str):
                        status_tokens.append(v.upper())
                err_str = str(result.get("error") or "").lower()
                err_class = str(result.get("error_class") or "").lower()
                # "Retryable" / soft failures — user/AI can adjust parameters.
                # 2026-05-20: explicit hard-reject error_class short-circuit — regardless
                # of what the error string looks like, these classes always count as hard,
                # preventing future error messages containing keywords like "too large" from
                # being incorrectly classified as soft.
                soft_failure = (
                    err_class not in _HARD_REJECT_ERROR_CLASSES
                    and (
                        "timeout" in err_str or "timed out" in err_str
                        or "retry budget" in err_str
                        or "payload_too_large" in err_class
                        or "too large" in err_str
                        or result.get("row_count") == 0  # empty result is not a connector failure
                        or result.get("found") == 0
                        or any(s == "EMPTY" for s in status_tokens)
                    )
                )
                empty_failure = _is_failed_or_empty_data_fetch(result)
                hard_failure = (
                    result.get("success") is False
                    or bool(result.get("error"))
                    or any(s in {"FAILED", "UNAVAILABLE"} for s in status_tokens)
                ) and not soft_failure

                if empty_failure:
                    empty_data_fetches.add(tool_name)
                if hard_failure:
                    tool_failure_counts[tool_name] = tool_failure_counts.get(tool_name, 0) + 1

            # G3.2: if any data-fetch failed this turn and the AI now runs
            # run_python without declaring a real source, treat that call as
            # EMPTY instead of showing a synthetic/demo replacement.  The
            # intended next step is an honest <tools_returned_nothing/>.
            if tool_name == "run_python" and isinstance(result, dict):
                failed_data_fetches = {
                    n for n, c in tool_failure_counts.items() if c > 0
                } | set(empty_data_fetches)
                declared = str(tc.get("input", {}).get("data_source", "")).strip()
                is_real_source_declared = declared in {
                    "latest_adql", "latest_search", "latest_lightcurve",
                    "latest_sdss_sql", "latest_high_velocity_stars",
                } or declared.startswith(("cached:", "fits:"))
                declared_empty_dependency = bool(empty_data_fetches & {
                    "latest_adql": {"run_adql"},
                    "latest_search": {"search_objects", "get_object_info", "get_object_dossier"},
                    "latest_lightcurve": {"search_lightcurve"},
                    "latest_sdss_sql": {"run_sdss_sql"},
                    "latest_high_velocity_stars": {"query_high_velocity_stars"},
                }.get(declared, set()))
                origin = str(result.get("data_origin") or "").lower()
                has_real_origin = origin in {"real_archive", "cached_real", "user_uploaded"}
                # PART AC C3 — code-content reverse exemption.
                # M3 audit caught the SYNTHETIC banner mis-tagging a
                # run_python that was processing real cached literature
                # tables (`get_cached_results("latest_literature_tables")`).
                # The AI hadn't declared a `data_source` on input and an
                # earlier search_literature returned 0 hits (entered
                # failed_data_fetches), so G3.2 tainted the result. But
                # the code body explicitly read a real cache helper —
                # the right answer is to inspect the code, not just the
                # input.data_source declaration.
                reads_real_cache_in_code = _run_python_code_reads_real_cache(
                    str(tc.get("input", {}).get("code") or "")
                )
                if (
                    failed_data_fetches
                    and not user_requested_synthetic_demo
                    and not has_real_origin
                    and not reads_real_cache_in_code
                    and (not is_real_source_declared or declared_empty_dependency)
                ):
                    # Replace the payload so stdout from fallback/demo code
                    # cannot be displayed as if it were a successful analysis.
                    empty_banner = {
                        "__tool_status__": "EMPTY",
                        "__do_not_claim__": True,
                        "__message_to_model__": (
                            f"Tool `run_python` produced no citeable data because "
                            f"data-fetch tools {sorted(failed_data_fetches)} failed "
                            f"earlier this turn and this call did not read a real "
                            f"data source. You MUST NOT use any facts, numbers, "
                            f"historical context, literature priors, physical "
                            f"interpretations, or conclusions from this call. "
                            f"Emit <tools_returned_nothing/> with the failed tool "
                            f"names instead of substituting synthetic data."
                        ),
                        "__suggested_next_step__": (
                            "Report that the requested real data could not be retrieved "
                            "and ask the user to narrow the query or try again later."
                        ),
                        "data_origin": "unavailable",
                        "analysis_status": "empty",
                        "row_count": 0,
                        "success": True,
                    }
                    suppressed_stdout = str(result.get("stdout") or "")
                    result = dict(empty_banner)
                    if suppressed_stdout.strip():
                        result["suppressed_stdout_preview"] = suppressed_stdout[:500]
                    tc = {**tc, "result": result}
                    synthetic_run_python_count += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "empty_after_failed_fetch_total", 1.0,
                            failed_tool=",".join(sorted(failed_data_fetches))[:80],
                            agent=agent_name,
                        )
                    except Exception:
                        pass

            result_str = json.dumps(result, default=str)
            if len(result_str) > 16000:
                # Field-level truncation: recursively shrink long strings/lists
                # while preserving dict structure so the AI can keep analyzing.
                def _truncate_value(v, depth=0):
                    if depth > 4:
                        return "[depth-limit]"
                    if isinstance(v, str) and len(v) > 2000:
                        return v[:2000] + f"... [truncated {len(v) - 2000} chars]"
                    if isinstance(v, list):
                        if len(v) > 50:
                            return [_truncate_value(x, depth + 1) for x in v[:50]] + [
                                f"... [truncated {len(v) - 50} items]"
                            ]
                        return [_truncate_value(x, depth + 1) for x in v]
                    if isinstance(v, dict):
                        return {k: _truncate_value(val, depth + 1) for k, val in v.items()}
                    return v
                truncated_result = _truncate_value(result) if isinstance(result, (dict, list)) else result
                result_str = json.dumps(truncated_result, default=str)
                # If still too large, emit a valid JSON envelope with a text preview.
                # The previous hard cap spliced raw bytes onto a JSON string, which
                # breaks whenever the cut falls inside a string literal or escape
                # sequence — corrupting the LLM's tool-result input.
                if len(result_str) > 24000:
                    result_str = json.dumps({
                        "__truncated__": True,
                        "original_size": len(result_str),
                        "preview": result_str[:20000],
                        "note": "Result exceeded 24 KB after field-level truncation; see preview.",
                    })
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                }
            )
            all_tool_results.append(
                {
                    "id": tc["id"],
                    "tool": tc["name"],
                    "input": tc["input"],
                    "result": result,
                }
            )
            internal_suppressed = (
                isinstance(result, dict)
                and bool(result.get("__internal_suppressed__"))
            )
            checkpoint_event = None if internal_suppressed else _record_tool_checkpoint(
                chat_session_id=chat_session_id,
                python_session_id=python_session_id,
                tool_call=tc,
                result=result,
            )
            if checkpoint_event is not None:
                await _emit({
                    "type": "workflow_checkpoint",
                    "agent": agent_name,
                    **checkpoint_event,
                })
            # Stream the result immediately so the UI can update inline.
            # `live: true` distinguishes this from the final consolidated
            # tool_result events the SSE generator emits at the end — the
            # frontend uses the flag to deduplicate (live -> thinking UI,
            # final -> actions list).
            if not internal_suppressed:
                await _emit({
                    "type": "tool_result",
                    "agent": agent_name,
                    "tool": tc["name"],
                    "result": result,
                    "live": True,
                    "tool_call_id": tc["id"],
                })
        working_messages.append({"role": "user", "content": tool_result_blocks})
        # Claude uses "tool_use", OpenAI uses "tool_calls" as stop reason
        if (
            not forced_tool_call_override
            and response.get("stop_reason") not in ("tool_use", "tool_calls")
        ):
            last_stop_reason = response.get("stop_reason")
            break

    full_reply = "\n\n".join(text_parts)

    # C-X1 / PART AC C2 — truncation gate (max_tokens OR text-shape).
    # When the model's reply hits the max_tokens budget mid-sentence
    # (e.g. stops on a colon: "Let me extract the next table:") the old
    # code shipped the dangling text straight to the UI as if it were
    # a finished reply. This is the R2.6 / R2.10 / M3 silent-truncation
    # regression.
    #
    # Two detection paths:
    # 1. Provider stop_reason = max_tokens (Anthropic) / length (OpenAI / DeepSeek).
    # 2. PART AC C2: text-shape — the reply self-evidently ends mid-sentence
    #    (trailing colon / comma / em-dash / connective word) even when
    #    the provider claimed stop_reason="stop". M3 audit caught this
    #    exact case: stop_reason was clean but the prose ended on
    #    "Let me search for additional [CII] datasets:".
    truncated_by_stop_reason = last_stop_reason in {"max_tokens", "length"}
    truncated_by_shape = (
        not truncated_by_stop_reason
        and _reply_looks_truncated(full_reply)
    )
    if (truncated_by_stop_reason or truncated_by_shape) and full_reply.strip():
        truncation_reason = (
            str(last_stop_reason) if truncated_by_stop_reason else "text_shape"
        )
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "max_tokens_truncation_total", 1.0,
                agent=agent_name, stop_reason=truncation_reason,
            )
        except Exception:
            pass
        await _emit({
            "type": "reply_truncated",
            "agent": agent_name,
            "stop_reason": truncation_reason,
            "partial_chars": len(full_reply),
        })
        try:
            safe_summary_user = (
                "Your previous reply was cut off mid-sentence by the "
                "model's max_tokens limit. In <= 3 sentences, write a "
                "summary that:\n"
                "1) Names what you actually completed using tool_results "
                "this turn (cite bibcodes / cache_keys when relevant).\n"
                "2) Names what you were ABOUT to do but did not complete "
                "(e.g. 'I had not yet called compare_luminosity_distances "
                "for Riess 2011').\n"
                "3) Suggests the user re-prompts with the next concrete "
                "step. Do NOT continue the original analysis — just "
                "summarise + hand off."
            )
            summary_messages = list(working_messages) + [
                {"role": "assistant", "content": full_reply},
                {"role": "user", "content": safe_summary_user},
            ]
            summary_resp = await inference_router.route(
                agent_name,
                summary_messages,
                system=system,
                tools=[],  # text-only — no tool calls allowed
                api_key=provider_api_keys.get("anthropic", ""),
                provider_api_keys=provider_api_keys,
                preferred_backend=preferred_backend,
                model_profile=model_profile,
                max_tokens=400,
                temperature=0.0,
                backend_timeout=30.0,
            )
            text_blocks = summary_resp.get("text") or summary_resp.get("content") or ""
            safe_summary = str(text_blocks).strip()
        except Exception as exc:
            logger.warning("safe-summary regen failed: %s", exc)
            safe_summary = ""

        truncation_banner = (
            "\n\n---\n\n"
            "*[The model's reply was cut off mid-sentence by its "
            "max_tokens limit. The platform regenerated a safe summary "
            "of what was actually completed and what remains to do "
            "next:]*\n\n"
        )
        if safe_summary:
            full_reply = full_reply.rstrip() + truncation_banner + safe_summary
        else:
            full_reply = full_reply.rstrip() + (
                "\n\n---\n\n*[Reply was truncated mid-sentence by the "
                "max_tokens limit; safe-summary regen also returned "
                "empty. Please re-ask with a narrower scope.]*"
            )

    actions = _parse_actions(full_reply)
    clean_reply = _strip_actions_from_reply(full_reply)

    # F2.3: structured abstention parser.  If the model emitted a single
    # <tools_returned_nothing/> tag as its reply, that IS the expected
    # response for empty/failed turns — skip the claim validator entirely
    # and render a canonical abstention card.
    abstention_payload = _parse_abstention_tag(clean_reply)
    if abstention_payload is not None:
        reason = _classify_abstention_reason(all_tool_results)
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "honest_abstention_total", 1.0,
                agent=agent_name, reason=reason,
            )
            record_counter("structured_abstention_emitted_total", 1.0, agent=agent_name)
        except Exception:
            pass
        logger.info(
            "Honest abstention emitted by %s (reason=%s): failed=%s empty=%s",
            agent_name, reason,
            abstention_payload.get("failed_tools", ""),
            abstention_payload.get("empty_tools", ""),
        )
        clean_reply = _render_abstention_card(abstention_payload, reason)
        if on_event is not None:
            try:
                await on_event({
                    "type": "honest_abstention",
                    "payload": {
                        **abstention_payload,
                        "reason": reason,
                        "agent": agent_name,
                    },
                })
            except Exception:
                pass
        # Attach tool-result action cards as usual, then short-circuit out.
        actions.extend(_tool_results_to_actions(all_tool_results))
        return {
            "reply": clean_reply,
            "actions": actions,
            "tool_results": all_tool_results,
            "hit_iteration_cap": False,
            "hit_deadline": hit_deadline,
            "honest_abstention": True,
            "abstention_reason": reason,
        }
    if "<tools_returned_nothing" in clean_reply or "<toolsreturnednothing" in clean_reply:
        clean_reply = _sanitize_tools_returned_nothing(clean_reply)

    # R2: zero-fabrication gate.  Validate every numeric claim in the reply
    # against the tool_results collected this turn; if any claim can't be
    # cited, push the LLM to regenerate.  After two failures, block.
    fabrication_stats = {"pass": 0, "blocked": False, "regenerations": 0}
    if clean_reply.strip() and not skip_claim_gate_for_meta:
        from app.services.claim_validator import (
            validate_claims,
            build_regeneration_prompt,
            build_english_regeneration_prompt,
            build_zero_data_qualitative_regeneration_prompt,
            blocked_reply_with_narrative,
            attach_draft_to_banner,
            zero_data_but_quantitative,
            is_empty_turn,
            literature_prior_violations,
            reply_contains_cjk,
            provenance_citation_violations,
            citation_violations_should_block,
            blocked_citation_reply_text,
            unsupported_literature_narrative_violations,
            blocked_unsupported_narrative_reply_text,
            # Stage 6 P0c-C: second-pass hard barrier
            unclassified_literature_violations,
            blocked_unclassified_literature_reply_text,
            # M6: methodology cross-check (Bayesian / demagnify count)
            methodology_consistency_violations,
        )

        # F1.4: zero-data hard block.  If every tool call this turn was
        # failed / empty / errored but the reply still makes numeric
        # claims, short-circuit to the block path.  Pleiades case: AI
        # wrote "776 stars, 7.353 ± 0.001 mas" after run_adql returned
        # 0 rows and run_python crashed — the regen loop would have
        # laundered the claim by rewriting with different phrasing.
        # X (PART X plan D): force replies to English. Any final reply containing
        # CJK / Japanese / Korean / full-width punctuation (threshold: 3 characters)
        # is hard-blocked immediately. This is the highest-priority branch — because
        # downstream zero-hallucination gate regexes are almost entirely English,
        # Chinese prose bypasses all claim extraction. The prompt already instructs
        # the AI that "reply must be English"; this is the hard-constraint safety net.
        cjk_detected = reply_contains_cjk(clean_reply)
        if cjk_detected:
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "fabrication_blocked_total",
                    1.0,
                    agent=agent_name,
                    reason="non_english_reply",
                )
            except Exception:
                pass
            # PART X plan D (revised): instead of hard-blocking a non-English
            # draft outright, attempt one English regeneration that preserves
            # every number and citation.  Only fall back to the hard block if
            # the rewrite fails or is still non-English.  Keeps non-English
            # prompts useful while preserving the English-only contract the
            # downstream zero-fabrication regex gate depends on.
            english_retry = ""
            try:
                english_messages = list(working_messages) + [
                    {"role": "assistant", "content": clean_reply},
                    {"role": "user", "content": build_english_regeneration_prompt()},
                ]
                english_regen = await _llm_messages_create(
                    system=system,
                    messages=english_messages,
                    tools=[],
                    provider_api_keys=provider_api_keys,
                    agent_name=agent_name,
                    preferred_backend=preferred_backend,
                    model_profile=model_profile,
                )
                english_retry = str(english_regen.get("content", "") or "").strip()
            except Exception as exc:
                logger.warning("English regeneration failed: %s", exc)
            if english_retry and not reply_contains_cjk(english_retry):
                clean_reply = english_retry
                cjk_detected = False
                fabrication_stats["regenerations"] += 1
                logger.info(
                    "Non-English reply from %s recovered via one English "
                    "regeneration",
                    agent_name,
                )
            else:
                logger.error(
                    "Non-English reply from %s — hard-blocking after failed "
                    "English regeneration (CJK / full-width characters "
                    "detected; platform contract requires English-only)",
                    agent_name,
                )
                clean_reply = (
                    "⚠ Reply blocked by platform policy: the assistant's final "
                    "reply must be in standard English.  Non-English characters "
                    "(Chinese / Japanese / Korean / full-width) were detected "
                    "in the draft reply.\n\n"
                    "This is a one-time notice; the assistant will regenerate "
                    "in English on the next turn.  If you prefer a different "
                    "language, please use an external translator — this is a "
                    "research-tool platform and English is the working "
                    "language for citation integrity (the zero-fabrication "
                    "numeric-claim gate only ships English regex patterns)."
                )
                fabrication_stats["blocked"] = True

        zero_data_claims = [] if cjk_detected else zero_data_but_quantitative(clean_reply, all_tool_results)
        if zero_data_claims:
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "zero_data_but_claims_total",
                    1.0,
                    agent=agent_name,
                    claim_count=str(len(zero_data_claims)),
                )
            except Exception:
                pass
            logger.error(
                "Zero-data turn with %d quantitative claim(s) from %s — "
                "hard-blocking: %s",
                len(zero_data_claims), agent_name,
                [c.label for c in zero_data_claims],
            )
            validation = validate_claims(clean_reply, all_tool_results)
            qualitative_rewrite = ""
            if validation.uncited:
                # PART AH: zero-data turns can still answer methodological
                # questions qualitatively.  Try one text-only rewrite that
                # strips every unsupported number instead of immediately
                # replacing the whole answer with a withheld block.
                rewrite_messages = list(working_messages) + [
                    {"role": "assistant", "content": clean_reply},
                    {
                        "role": "user",
                        "content": build_zero_data_qualitative_regeneration_prompt(validation),
                    },
                ]
                try:
                    regen = await _llm_messages_create(
                        system=system,
                        messages=rewrite_messages,
                        tools=[],
                        provider_api_keys=provider_api_keys,
                        agent_name=agent_name,
                        preferred_backend=preferred_backend,
                        model_profile=model_profile,
                    )
                    qualitative_rewrite = str(regen.get("content", "") or "").strip()
                except Exception as exc:
                    logger.warning("Zero-data qualitative rewrite failed: %s", exc)

            if qualitative_rewrite:
                rewrite_validation = validate_claims(qualitative_rewrite, all_tool_results)
                rewrite_zero_data_claims = zero_data_but_quantitative(
                    qualitative_rewrite, all_tool_results,
                )
                rewrite_citation_violations = provenance_citation_violations(
                    qualitative_rewrite, all_tool_results,
                )
                rewrite_unsupported_narrative = unsupported_literature_narrative_violations(
                    qualitative_rewrite, all_tool_results,
                )
                if (
                    rewrite_validation.ok
                    and not rewrite_zero_data_claims
                    and not citation_violations_should_block(rewrite_citation_violations)
                    and not rewrite_unsupported_narrative
                ):
                    clean_reply = qualitative_rewrite
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "zero_data_qualitative_rewrite_total",
                            1.0,
                            agent=agent_name,
                        )
                    except Exception:
                        pass
                elif rewrite_citation_violations and citation_violations_should_block(rewrite_citation_violations):
                    # Stage 6 P0a follow-up: preserve AI's rewrite narrative
                    # (banner-only behavior dropped the draft entirely)
                    clean_reply = attach_draft_to_banner(
                        blocked_citation_reply_text(rewrite_citation_violations),
                        qualitative_rewrite,
                    )
                    fabrication_stats["blocked"] = True
                elif rewrite_unsupported_narrative:
                    # Stage 6 P0a follow-up: preserve AI's rewrite narrative
                    clean_reply = attach_draft_to_banner(
                        blocked_unsupported_narrative_reply_text(rewrite_unsupported_narrative),
                        qualitative_rewrite,
                    )
                    fabrication_stats["blocked"] = True
                else:
                    # Stage 6 P0: preserve AI's qualitative rewrite narrative
                    clean_reply = blocked_reply_with_narrative(
                        rewrite_validation, qualitative_rewrite,
                    )
                    fabrication_stats["blocked"] = True
            else:
                # Stage 6 P0: preserve AI's original reply narrative
                clean_reply = blocked_reply_with_narrative(validation, clean_reply)
                fabrication_stats["blocked"] = True
            try:
                from app.observability.metrics import record_counter
                if fabrication_stats["blocked"]:
                    record_counter(
                        "fabrication_blocked_total",
                        1.0,
                        agent=agent_name,
                        reason="zero_data",
                    )
            except Exception:
                pass

        elif (
            unsupported_narrative_claims := unsupported_literature_narrative_violations(
                clean_reply, all_tool_results
            )
        ):
            logger.error(
                "Unsupported narrative gate BLOCKED reply from %s (%d violations)",
                agent_name,
                len(unsupported_narrative_claims),
            )
            tool_grounded_summary = (
                _research_tool_grounded_summary(all_tool_results)
                or _line_lfr_tool_grounded_summary(all_tool_results)
                or _statistics_tool_grounded_summary(all_tool_results)
                or _cosmology_tool_grounded_summary(all_tool_results)
            )
            if tool_grounded_summary:
                summary_validation = validate_claims(tool_grounded_summary, all_tool_results)
                summary_citation_violations = provenance_citation_violations(
                    tool_grounded_summary, all_tool_results,
                )
                summary_unsupported = unsupported_literature_narrative_violations(
                    tool_grounded_summary, all_tool_results,
                )
                if (
                    summary_validation.ok
                    and not citation_violations_should_block(summary_citation_violations)
                    and not summary_unsupported
                ):
                    clean_reply = tool_grounded_summary
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "tool_grounded_regeneration_total",
                            1.0,
                            agent=agent_name,
                            reason="unsupported_narrative",
                        )
                    except Exception:
                        pass
                else:
                    # Stage 6 P0a follow-up: preserve AI's reply narrative
                    clean_reply = attach_draft_to_banner(
                        blocked_unsupported_narrative_reply_text(unsupported_narrative_claims),
                        clean_reply,
                    )
                    fabrication_stats["blocked"] = True
            else:
                # Stage 6 P0a follow-up: preserve AI's reply narrative
                clean_reply = attach_draft_to_banner(
                    blocked_unsupported_narrative_reply_text(unsupported_narrative_claims),
                    clean_reply,
                )
                fabrication_stats["blocked"] = True

        elif (
            unclassified_claims := unclassified_literature_violations(
                clean_reply, all_tool_results
            )
        ):
            # Stage 6 P0c-C (2026-05-19): hard barrier. The LLM must first call
            # classify_literature_relevance before citing a search_literature paper
            # in the narrative. If it didn't call it / cited an Off-topic paper,
            # the whole section is blocked with a banner and the draft is preserved.
            logger.error(
                "Unclassified literature gate BLOCKED reply from %s (%d violations)",
                agent_name, len(unclassified_claims),
            )
            clean_reply = attach_draft_to_banner(
                blocked_unclassified_literature_reply_text(unclassified_claims),
                clean_reply,
            )
            fabrication_stats["blocked"] = True
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "fabrication_blocked_total",
                    1.0,
                    agent=agent_name,
                    reason="unclassified_literature",
                )
            except Exception:
                pass

        elif literature_prior_violations(clean_reply, all_tool_results):
            # W1 (PART W): hard literature-prior block. Looser than
            # zero_data_but_quantitative — here tool_results contain data, but
            # the claim is a quantity like age/mass/distance that requires a
            # dedicated measurement tool. If that tool wasn't run this turn,
            # the block fires to prevent the regen loop from laundering the
            # number with a coincidentally matching universe value (Pleiades
            # "~100 Myr" scenario).
            lit_prior_claims = literature_prior_violations(
                clean_reply, all_tool_results
            )
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "fabrication_blocked_total",
                    1.0,
                    agent=agent_name,
                    reason="literature_prior",
                )
            except Exception:
                pass
            logger.error(
                "Literature-prior turn with %d claim(s) from %s — "
                "hard-blocking (labels: %s, tools_run this turn: %s)",
                len(lit_prior_claims), agent_name,
                [c.label for c in lit_prior_claims],
                sorted({
                    r["tool"] for r in all_tool_results
                    if isinstance(r, dict) and "tool" in r
                }),
            )
            validation = validate_claims(clean_reply, all_tool_results)
            # Stage 6 P0: keep AI's reply narrative; banner + extra note still
            # appear on top, narrative (with unverified numbers redacted) below.
            _original_reply_lit_anchor = clean_reply
            clean_reply = blocked_reply_with_narrative(
                validation, _original_reply_lit_anchor,
            )
            # Insert the literature-anchor guidance right after the banner,
            # before the "---" divider that precedes the narrative.
            _divider = "\n\n---\n\n"
            _additional_note = (
                "\n\nAdditional note: your claims matched the pattern of "
                "citing a textbook literature value (age / mass / distance) "
                "without running a corresponding measurement tool this turn. "
                "If you want to cite a literature value, run a literature "
                "search first so the citation is present in this turn. If "
                "you want to measure it, run the corresponding measurement "
                "workflow explicitly."
            )
            if _divider in clean_reply:
                _banner_part, _narrative_part = clean_reply.split(_divider, 1)
                clean_reply = _banner_part + _additional_note + _divider + _narrative_part
            else:
                clean_reply = clean_reply + _additional_note
            fabrication_stats["blocked"] = True

        elif _unsupported_cosmology_anchor_numeric_comparison(clean_reply, all_tool_results):
            logger.error(
                "Unsupported cosmology anchor comparison from %s — replacing with grounded summary",
                agent_name,
            )
            tool_grounded_summary = (
                _research_tool_grounded_summary(all_tool_results)
                or _cosmology_tool_grounded_summary(all_tool_results)
            )
            if tool_grounded_summary:
                summary_validation = validate_claims(tool_grounded_summary, all_tool_results)
                summary_citation_violations = provenance_citation_violations(
                    tool_grounded_summary,
                    all_tool_results,
                )
                if (
                    summary_validation.ok
                    and not citation_violations_should_block(summary_citation_violations)
                    and not _unsupported_cosmology_anchor_numeric_comparison(
                        tool_grounded_summary,
                        all_tool_results,
                    )
                ):
                    clean_reply = tool_grounded_summary
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "tool_grounded_regeneration_total",
                            1.0,
                            agent=agent_name,
                            reason="unsupported_cosmology_anchor",
                        )
                    except Exception:
                        pass
                else:
                    # Stage 6 P0: preserve tool_grounded_summary narrative
                    clean_reply = blocked_reply_with_narrative(
                        summary_validation, tool_grounded_summary,
                    )
                    fabrication_stats["blocked"] = True
            else:
                validation = validate_claims(clean_reply, all_tool_results)
                # Stage 6 P0: preserve AI's original reply narrative
                clean_reply = blocked_reply_with_narrative(validation, clean_reply)
                fabrication_stats["blocked"] = True

        elif True:
            citation_violations = provenance_citation_violations(clean_reply, all_tool_results)
            # M6 + PART AB: methodology mismatches (Bayesian promised but
            # OLS ran, demagnify count claimed > actual) gate the reply
            # the same way citation violations do, but render through a
            # DIFFERENT blocked-reply text (`blocked_methodology_reply_text`)
            # so the user gets the right fix instruction. Pre-PART-AB the
            # method violations were rendered through the citation text,
            # which told the user to "re-run the archive query" — that's
            # the wrong fix for a methodology mismatch (no archive query
            # makes the word "Bayesian" appear in tool_results).
            method_violations = methodology_consistency_violations(
                clean_reply, all_tool_results,
            )
            all_violations = list(citation_violations) + list(method_violations)
            if all_violations and citation_violations_should_block(all_violations):
                logger.error(
                    "Citation/methodology gate BLOCKED reply from %s "
                    "(citation=%d, method=%d)",
                    agent_name,
                    len(citation_violations),
                    len(method_violations),
                )
                tool_grounded_summary = (
                    _research_tool_grounded_summary(all_tool_results)
                    or _line_lfr_tool_grounded_summary(all_tool_results)
                    or _statistics_tool_grounded_summary(all_tool_results)
                    or _cosmology_tool_grounded_summary(all_tool_results)
                )
                recovered_with_summary = False
                if tool_grounded_summary:
                    summary_validation = validate_claims(tool_grounded_summary, all_tool_results)
                    summary_citation_violations = provenance_citation_violations(
                        tool_grounded_summary,
                        all_tool_results,
                    )
                    summary_method_violations = methodology_consistency_violations(
                        tool_grounded_summary,
                        all_tool_results,
                    )
                    summary_violations = list(summary_citation_violations) + list(summary_method_violations)
                    if (
                        summary_validation.ok
                        and not citation_violations_should_block(summary_violations)
                    ):
                        clean_reply = tool_grounded_summary
                        recovered_with_summary = True
                        fabrication_stats["regenerations"] += 1
                        try:
                            from app.observability.metrics import record_counter
                            record_counter(
                                "tool_grounded_regeneration_total",
                                1.0,
                                agent=agent_name,
                                reason="citation_methodology",
                            )
                        except Exception:
                            pass

                if not recovered_with_summary:
                    annotations: list[str] = []
                    if citation_violations:
                        annotations.append(blocked_citation_reply_text(citation_violations))
                    if method_violations:
                        from app.services.claim_validator import (
                            blocked_methodology_reply_text,
                        )
                        annotations.append(blocked_methodology_reply_text(method_violations))

                    # PART AG C1 — annotate-and-attach mode (replaces the
                    # earlier withhold-all behaviour).
                    #
                    # R2.4 M6 audit caught the earlier path erasing 11 of 12
                    # visible tool cards: the model wrote a long correct
                    # prose with Python output, dataframe loads, fit_line_lfr
                    # numbers, then mentioned "Bothwell 2013" on a single line
                    # without a tool_result → guard tripped → entire reply
                    # replaced by "Reply withheld" → user lost the whole
                    # session's worth of work for one inline citation slip.
                    #
                    # Prefer a grounded deterministic summary when available.
                    # If there is no safe summary, keep the original prose and
                    # APPEND a footer with provenance violations so tool cards
                    # and real analysis remain visible.
                    if clean_reply.strip():
                        annotation_block = (
                            "\n\n---\n\n"
                            "## ⚠ Citation / methodology provenance check failed\n\n"
                            "The reply above was generated, but the platform's "
                            "provenance gate flagged claims that the tool results "
                            "this turn did not support. Treat the flagged items as "
                            "**NOT verified** and re-run the relevant tools before "
                            "quoting any of them in a paper.\n\n"
                            + "\n\n---\n\n".join(annotations)
                        )
                        clean_reply = clean_reply.rstrip() + annotation_block
                    else:
                        # Empty prose — rare but possible (e.g. the LLM
                        # returned only tool_use blocks). Fall back to the
                        # previous withhold-only message so the user has
                        # something to read.
                        clean_reply = "\n\n---\n\n".join(annotations)
                    fabrication_stats["blocked"] = True

            if not fabrication_stats["blocked"]:
                for attempt in range(2):
                    validation = validate_claims(clean_reply, all_tool_results)
                    if validation.ok:
                        break
                    fabrication_stats["pass"] = attempt + 1
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "fabrication_detected_total",
                            1.0,
                            agent=agent_name,
                            attempt=str(attempt + 1),
                        )
                    except Exception:
                        pass
                    logger.warning(
                        "Fabrication detected in %s reply (attempt %d): %d uncited claim(s): %s",
                        agent_name, attempt + 1, len(validation.uncited),
                        [c.label for c in validation.uncited],
                    )
                    # Push the correction as a follow-up user message; no tools.
                    working_messages.append({
                        "role": "assistant",
                        "content": clean_reply,
                    })
                    working_messages.append({
                        "role": "user",
                        "content": build_regeneration_prompt(validation),
                    })
                    try:
                        regen = await _llm_messages_create(
                            system=system,
                            messages=working_messages,
                            tools=[],  # no tools — prose rewrite only
                            provider_api_keys=provider_api_keys,
                            agent_name=agent_name,
                            preferred_backend=preferred_backend,
                            model_profile=model_profile,
                        )
                        regenerated = str(regen.get("content", "") or "").strip()
                    except Exception as exc:
                        logger.warning("Regeneration call failed: %s", exc)
                        break
                    if not regenerated:
                        break
                    clean_reply = regenerated
                    text_parts.append("\n[regenerated]\n" + regenerated)
                else:
                    # Two attempts did not cure it — block the reply entirely.
                    validation = validate_claims(clean_reply, all_tool_results)
                    if not validation.ok:
                        tool_grounded_summary = (
                            _research_tool_grounded_summary(all_tool_results)
                            or _line_lfr_tool_grounded_summary(all_tool_results)
                            or _statistics_tool_grounded_summary(all_tool_results)
                            or _cosmology_tool_grounded_summary(all_tool_results)
                        )
                        if tool_grounded_summary:
                            summary_validation = validate_claims(
                                tool_grounded_summary, all_tool_results,
                            )
                            if summary_validation.ok:
                                clean_reply = tool_grounded_summary
                                validation = summary_validation
                                fabrication_stats["regenerations"] += 1
                                try:
                                    from app.observability.metrics import record_counter
                                    record_counter(
                                        "tool_grounded_regeneration_total",
                                        1.0,
                                        agent=agent_name,
                                        reason="regen_exhausted",
                                    )
                                except Exception:
                                    pass
                        if not validation.ok:
                            try:
                                from app.observability.metrics import record_counter
                                record_counter("fabrication_blocked_total", 1.0, agent=agent_name, reason="regen_exhausted")
                            except Exception:
                                pass
                            logger.error(
                                "Fabrication gate BLOCKED reply from %s (%d uncited)",
                                agent_name, len(validation.uncited),
                            )
                            # Stage 6 P0: keep AI's regen-exhausted narrative
                            # so the user sees methodology / caveats, not just
                            # the banner. Uncited numbers are redacted in-place.
                            clean_reply = blocked_reply_with_narrative(
                                validation, clean_reply,
                            )
                            fabrication_stats["blocked"] = True
                if fabrication_stats["regenerations"]:
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "reply_regeneration_total", float(fabrication_stats["regenerations"]),
                            agent=agent_name,
                        )
                    except Exception:
                        pass
        # F1.2: track whether this turn ran the claim gate so the
        # fallback-synthesis branch below knows to apply it too.
        _claim_gate_ran = True
    else:
        _claim_gate_ran = False
        # Still need is_empty_turn for the fallback branch below.
        from app.services.claim_validator import is_empty_turn  # noqa: F401

    research_mode_result_present = research_program_workflow or any(
        isinstance(tr, dict)
        and tr.get("tool") in {
            "plan_research_program",
            "run_research_matrix",
            "build_evidence_graph",
        }
        for tr in all_tool_results
    )

    if research_mode_result_present and clean_reply.strip():
        try:
            from app.services.research_program import verify_research_facts

            fact_input = {
                "tool_results": _compact_tool_results_for_evidence(all_tool_results),
                "final_reply": clean_reply,
            }
            fact_result = verify_research_facts(**fact_input)
            fact_tool_result = {
                "id": f"auto_fact_check_{uuid.uuid4().hex}",
                "tool": "verify_research_facts",
                "input": fact_input,
                "result": fact_result,
            }
            all_tool_results.append(fact_tool_result)
            if fact_result.get("status") == "blocked":
                safe_summary = _research_tool_grounded_summary(all_tool_results)
                if safe_summary:
                    clean_reply = safe_summary
                else:
                    clean_reply = (
                        "The research run completed, but fact verification found "
                        "a contradicted claim in the draft. Please review the Fact "
                        "Check card and rerun the missing evidence path before "
                        "using the result."
                    )
                try:
                    from app.observability.metrics import record_counter
                    record_counter(
                        "fabrication_blocked_total",
                        1.0,
                        agent=agent_name,
                        reason="fact_verification",
                    )
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Research fact verification skipped: %s", exc)

    if research_mode_result_present:
        # Research Mode answers must be the evidence graph's public surface, not
        # a fresh model-written literature review.  The model is still used to
        # choose and run tools, but the final prose is deterministic so it
        # cannot introduce unsupported paper facts, citations, or background
        # claims after the tools have completed.
        tool_grounded_research_summary = _research_tool_grounded_summary(all_tool_results)
        if tool_grounded_research_summary:
            clean_reply = tool_grounded_research_summary

    if research_mode_result_present and not any(
        tr.get("tool") == "export_research_report"
        for tr in all_tool_results
    ):
        try:
            from app.services.research_program import export_research_report

            report_input = {
                "research_plan": _research_plan_from_tool_results(all_tool_results),
                "evidence_graph": _research_evidence_graph_from_tool_results(all_tool_results),
                "tool_results": all_tool_results,
                "title": latest_user_text[:180] if latest_user_text else None,
            }
            report_result = export_research_report(**report_input)
            all_tool_results.append({
                "id": f"auto_research_report_{uuid.uuid4().hex}",
                "tool": "export_research_report",
                "input": {
                    "research_plan": report_input["research_plan"],
                    "evidence_graph": report_input["evidence_graph"],
                    "title": report_input["title"],
                },
                "result": report_result,
            })
        except Exception as exc:
            logger.warning("Research report export skipped: %s", exc)

    actions.extend(_tool_results_to_actions(all_tool_results))

    # Fallback: if the LLM returned zero text (empty text_parts) but did
    # execute tools, synthesise a minimal human-readable summary so the user
    # never sees a blank AI bubble. Root cause of the empty-response bug
    # observed in the WD LF test (first attempt).
    if not clean_reply.strip():
        research_summary = _research_tool_grounded_summary(all_tool_results)
        if research_summary:
            clean_reply = research_summary
        else:
            line_lfr_summary = _line_lfr_tool_grounded_summary(all_tool_results)
            if line_lfr_summary:
                clean_reply = line_lfr_summary
            else:
                stats_summary = _statistics_tool_grounded_summary(all_tool_results)
                if stats_summary:
                    clean_reply = stats_summary
        if not clean_reply.strip():
            cosmology_summary = _cosmology_tool_grounded_summary(all_tool_results)
            if cosmology_summary:
                clean_reply = cosmology_summary
        if not clean_reply.strip():
            if all_tool_results:
                tool_names = ", ".join({tr["tool"] for tr in all_tool_results})
                clean_reply = (
                    f"I ran the following tools: {tool_names}. "
                    f"The results are shown below. "
                    f"(Note: the language model did not return a written summary — "
                    f"please review the tool outputs directly or ask me to explain them.)"
                )
            else:
                clean_reply = (
                    "The language model returned an empty response. This may be a "
                    "transient API issue or a prompt length problem. Please try "
                    "again with a shorter prompt, or contact support if it persists."
                )
        logger.warning(
            "Empty AI reply detected in %s agent loop; synthesised fallback. "
            "tool_results=%d iterations=%d",
            agent_name, len(all_tool_results), _iteration + 1,
        )

        # F1.2: even the synthesised fallback must not smuggle numbers past
        # the validation gate.  The summary above is tool-name-only so it
        # should pass trivially, but if any downstream change adds
        # numerics to this branch, run validate_claims so it gets caught.
        try:
            from app.services.claim_validator import (
                validate_claims,
                blocked_reply_with_narrative,
            )
            fallback_validation = validate_claims(clean_reply, all_tool_results)
            if not fallback_validation.ok:
                logger.error(
                    "Fallback synthesis contained %d uncited claim(s); "
                    "replacing with block message",
                    len(fallback_validation.uncited),
                )
                try:
                    from app.observability.metrics import record_counter
                    record_counter(
                        "fabrication_blocked_total",
                        1.0,
                        agent=agent_name,
                        reason="fallback_synthesis",
                    )
                except Exception:
                    pass
                # Stage 6 P0: preserve fallback-synthesis narrative
                clean_reply = blocked_reply_with_narrative(
                    fallback_validation, clean_reply,
                )
                fabrication_stats["blocked"] = True
        except Exception as e:
            logger.debug("Fallback synthesis validation skipped: %s", e)

    # M7: telemetry so the UI can surface "hit iteration cap" to the user
    # (previously silent — a 13-step workflow just got truncated with no
    # indication why).
    if _iteration + 1 >= max_iterations:
        hit_iteration_cap = True

    return {
        "reply": clean_reply,
        "actions": actions,
        "tool_results": all_tool_results,
        "hit_iteration_cap": hit_iteration_cap,
        "hit_deadline": hit_deadline,
    }


def _build_agent_handoff_message(handoff) -> str:
    return (
        f"Prior agent `{handoff.source_agent}` completed its step.\n"
        f"Context summary: {handoff.context_summary}\n"
        f"Instruction: {handoff.instruction}"
    )


async def _run_orchestrated_chat(
    *,
    runtime: dict,
    messages: list[dict],
    provider_api_keys: dict[str, str],
    python_session_id: str,
    preferred_backend: str | None = None,
    model_profile: ModelProfile | None = None,
    user_id: str | None = None,
    chat_session_id: str | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    workflow_budget: dict[str, Any] | None = None,
) -> dict:
    agent_names = list(runtime.get("agent_names") or [])
    if not agent_names:
        agent_names = ["orchestrator"]

    try:
        _loop_sig = inspect.signature(_run_agent_loop)
        _loop_accepts_workflow_budget = (
            "workflow_budget" in _loop_sig.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in _loop_sig.parameters.values())
        )
        _loop_accepts_model_profile = (
            "model_profile" in _loop_sig.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in _loop_sig.parameters.values())
        )
    except Exception:
        _loop_accepts_workflow_budget = True
        _loop_accepts_model_profile = True

    if len(agent_names) == 1:
        loop_kwargs = {
            "system": str(runtime.get("system", "") or ""),
            "messages": messages,
            "tools": list(runtime.get("toolset") or []),
            "provider_api_keys": provider_api_keys,
            "agent_name": agent_names[0],
            "python_session_id": python_session_id,
            "preferred_backend": preferred_backend,
            "user_id": user_id,
            "chat_session_id": chat_session_id,
            "on_event": on_event,
        }
        if _loop_accepts_model_profile:
            loop_kwargs["model_profile"] = model_profile
        if _loop_accepts_workflow_budget:
            loop_kwargs["workflow_budget"] = workflow_budget
        single = await _run_agent_loop(**loop_kwargs)
        return {
            "reply": single["reply"],
            "actions": single["actions"],
            "tool_results": single.get("tool_results", []),
            "hit_deadline": single.get("hit_deadline", False),
            "hit_iteration_cap": single.get("hit_iteration_cap", False),
        }

    agent_results: list[dict] = []
    handoff = None
    for index, agent_name in enumerate(agent_names):
        agent_runtime = orchestrator.get_agent_runtime(
            agent_name,
            str(runtime.get("user_context", "") or ""),
        )
        agent_messages = deepcopy(messages)
        if handoff is not None:
            agent_messages.append({"role": "user", "content": _build_agent_handoff_message(handoff)})
        loop_kwargs = {
            "system": str(runtime.get("base_system", "") or "") + "\n\n" + agent_runtime["system_prompt"],
            "messages": agent_messages,
            "tools": _filter_tools(agent_runtime.get("tool_names"), list(runtime.get("toolset") or [])),
            "provider_api_keys": provider_api_keys,
            "agent_name": agent_name,
            "python_session_id": python_session_id,
            "preferred_backend": preferred_backend,
            "user_id": user_id,
            "chat_session_id": chat_session_id,
            "on_event": on_event,
        }
        if _loop_accepts_model_profile:
            loop_kwargs["model_profile"] = model_profile
        if _loop_accepts_workflow_budget:
            loop_kwargs["workflow_budget"] = workflow_budget
        result = await _run_agent_loop(**loop_kwargs)
        agent_results.append(
            {
                "agent_name": agent_name,
                "reply": result["reply"],
                "actions": result["actions"],
                "tool_results": result.get("tool_results", []),
                "hit_deadline": result.get("hit_deadline", False),
                "hit_iteration_cap": result.get("hit_iteration_cap", False),
            }
        )
        if index < len(agent_names) - 1:
            handoff = await orchestrator.summarize_handoff(
                agent_name,
                agent_names[index + 1],
                result["reply"],
            )

    merged_actions: list[dict] = []
    merged_tool_results: list[dict] = []
    for result in agent_results:
        merged_actions.extend(result["actions"])
        merged_tool_results.extend(result.get("tool_results", []))
    merged_reply = await orchestrator.merge_responses(agent_results)
    if not merged_reply.strip():
        merged_reply = (
            _research_tool_grounded_summary(merged_tool_results)
            or _line_lfr_tool_grounded_summary(merged_tool_results)
            or _statistics_tool_grounded_summary(merged_tool_results)
            or _cosmology_tool_grounded_summary(merged_tool_results)
            or ""
        )
        if not merged_reply.strip() and merged_tool_results:
            tool_names = ", ".join({
                str(tr.get("tool") or tr.get("name") or "unknown")
                for tr in merged_tool_results
                if isinstance(tr, dict)
            })
            merged_reply = (
                f"I ran the following tools: {tool_names}. "
                "The results are shown below. The specialist agents did not "
                "return a merged written summary, so treat the tool cards as "
                "the source of truth and rerun if you need prose explanation."
            )
        if merged_reply.strip():
            logger.warning(
                "Empty merged AI reply detected; synthesised tool-grounded fallback. "
                "tool_results=%d agents=%d",
                len(merged_tool_results),
                len(agent_results),
            )
    if merged_reply.strip():
        try:
            from app.services.claim_validator import (
                blocked_reply_with_narrative,
                attach_draft_to_banner,
                blocked_citation_reply_text,
                blocked_unsupported_narrative_reply_text,
                citation_violations_should_block,
                provenance_citation_violations,
                unsupported_literature_narrative_violations,
                validate_claims,
                zero_data_but_quantitative,
            )

            # R21: specialist replies are individually checked inside each
            # agent loop, but the final merged prose is a new assistant reply.
            # Validate it against the union of tool results from the same user
            # turn so a later agent cannot accidentally launder unsupported
            # numbers from an earlier rewrite/handoff.
            zero_data_claims = zero_data_but_quantitative(merged_reply, merged_tool_results)
            unsupported_narrative = unsupported_literature_narrative_violations(
                merged_reply, merged_tool_results
            )
            citation_violations = provenance_citation_violations(merged_reply, merged_tool_results)
            validation = validate_claims(merged_reply, merged_tool_results)
            if unsupported_narrative:
                logger.error(
                    "Unsupported narrative gate BLOCKED merged reply (%d violations)",
                    len(unsupported_narrative),
                )
                # Stage 6 P0a follow-up: preserve merged-reply narrative
                merged_reply = attach_draft_to_banner(
                    blocked_unsupported_narrative_reply_text(unsupported_narrative),
                    merged_reply,
                )
            elif citation_violations and citation_violations_should_block(citation_violations):
                logger.error(
                    "Citation provenance gate BLOCKED merged reply (%d violations)",
                    len(citation_violations),
                )
                # PART AG C1 / PART AH C5 — annotate-and-attach in the
                # orchestrator merge path too. Pre-AH this site still
                # used the old withhold-all behaviour; M7 retest caught
                # an 18-tool / 348-char chat round whose entire prose
                # was wiped because the AI snuck "arXiv:1404.7159"
                # (a paper not in tool_results) into a citation list.
                # Same fix as the agent-loop path: keep the prose,
                # append a footer block.
                if merged_reply.strip():
                    merged_reply = merged_reply.rstrip() + (
                        "\n\n---\n\n"
                        "## ⚠ Citation provenance check failed\n\n"
                        "The reply above was generated, but the platform's "
                        "provenance gate flagged citations that the merged "
                        "tool_results did not support. Treat the flagged "
                        "items as **NOT verified** and re-run the relevant "
                        "tools before quoting them in a paper.\n\n"
                        + blocked_citation_reply_text(citation_violations)
                    )
                else:
                    merged_reply = blocked_citation_reply_text(citation_violations)
            elif zero_data_claims or not validation.ok:
                try:
                    from app.observability.metrics import record_counter
                    record_counter(
                        "fabrication_blocked_total",
                        1.0,
                        agent="merged_orchestrator",
                        reason="merged_reply",
                    )
                except Exception:
                    pass
                logger.error(
                    "Fabrication gate BLOCKED merged reply (%d uncited, zero_data=%s)",
                    len(validation.uncited),
                    bool(zero_data_claims),
                )
                # Stage 6 P0: preserve merged-reply narrative
                merged_reply = blocked_reply_with_narrative(validation, merged_reply)
        except Exception as exc:
            logger.warning("Merged-reply claim validation failed open: %s", exc)

    latest_research_user_text = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            latest_research_user_text = str(message.get("content") or "")
            break
    merged_research_workflow = _is_research_program_workflow(latest_research_user_text)
    if merged_research_workflow and merged_reply.strip():
        try:
            from app.services.research_program import verify_research_facts

            fact_input = {
                "tool_results": _compact_tool_results_for_evidence(merged_tool_results),
                "final_reply": merged_reply,
            }
            fact_result = verify_research_facts(**fact_input)
            fact_tool_result = {
                "id": f"auto_fact_check_{uuid.uuid4().hex}",
                "tool": "verify_research_facts",
                "input": fact_input,
                "result": fact_result,
            }
            merged_tool_results.append(fact_tool_result)
            merged_actions.extend(_tool_results_to_actions([fact_tool_result]))
            if fact_result.get("status") == "blocked":
                safe_summary = _research_tool_grounded_summary(merged_tool_results)
                if safe_summary:
                    merged_reply = safe_summary
                else:
                    merged_reply = (
                        "The research run completed, but fact verification found "
                        "a contradicted claim in the merged draft. Please review "
                        "the Fact Check card and rerun the missing evidence path "
                        "before using the result."
                    )
        except Exception as exc:
            logger.warning("Merged research fact verification skipped: %s", exc)

    if merged_research_workflow and not any(
        tr.get("tool") == "export_research_report"
        for tr in merged_tool_results
    ):
        try:
            from app.services.research_program import export_research_report

            report_input = {
                "research_plan": _research_plan_from_tool_results(merged_tool_results),
                "evidence_graph": _research_evidence_graph_from_tool_results(merged_tool_results),
                "tool_results": merged_tool_results,
                "title": latest_research_user_text[:180] if latest_research_user_text else None,
            }
            report_result = export_research_report(**report_input)
            report_tool_result = {
                "id": f"auto_research_report_{uuid.uuid4().hex}",
                "tool": "export_research_report",
                "input": {
                    "research_plan": report_input["research_plan"],
                    "evidence_graph": report_input["evidence_graph"],
                    "title": report_input["title"],
                },
                "result": report_result,
            }
            merged_tool_results.append(report_tool_result)
            merged_actions.extend(_tool_results_to_actions([report_tool_result]))
        except Exception as exc:
            logger.warning("Merged research report export skipped: %s", exc)
    return {
        "reply": merged_reply,
        "actions": merged_actions,
        "tool_results": merged_tool_results,
        "hit_deadline": any(bool(r.get("hit_deadline")) for r in agent_results),
        "hit_iteration_cap": any(bool(r.get("hit_iteration_cap")) for r in agent_results),
    }


# G7.3: debug store for the last LLM prompt.  Populated by the agent-loop
# `_llm_messages_create` when DEBUG_LAST_PROMPT env is set.  Admin-only
# endpoint returns it so the reviewer can verify in the browser that
# the anti-sim rule + anti-reflection rule + structured-abstention spec
# actually reach the model.
_LAST_PROMPT_DEBUG: dict[str, object] = {
    "enabled": False,
    "system": "",
    "message_count": 0,
    "first_messages_preview": [],
    "timestamp": "",
    "agent": "",
}


@router.get("/_debug_last_prompt", dependencies=[Depends(require_admin_any)])
async def debug_last_prompt(
    request: Request,
):
    """G7.3: return the last LLM prompt seen by the inference router.

    Only active when DEBUG_LAST_PROMPT=1 is set in the env (prod default
    is off — this is a diagnostic aid, not a production feature).
    """
    if not os.getenv("DEBUG_LAST_PROMPT", "").strip():
        return {
            "enabled": False,
            "note": (
                "Set DEBUG_LAST_PROMPT=1 in the backend env to enable this "
                "endpoint. Designed for confirming the zero-fabrication / "
                "anti-simulation rules actually appear in the LLM's prompt."
            ),
        }
    return dict(_LAST_PROMPT_DEBUG)


@router.get("/ai_backend_status")
async def ai_backend_status(
    request: Request,
    api_provider: str | None = Query(default=None),
    model_profile: str | None = Query(default=None),
    user: User | None = Depends(get_optional_user),
):
    """F4.2: report which AI backends are configured so the frontend can
    disable Send and show a setup CTA when nothing is available.

    Response is shape:
      {
        "configured_backends": ["anthropic", "openai"],
        "needs_setup": false,
      }
    We check (a) env vars set server-side and (b) any per-user keys the
    authenticated user has stored.  Never returns the keys themselves.
    """
    configured: list[str] = []
    # Server-side shared DeepSeek may be enabled for public chat. Other hosted
    # providers remain BYOK.
    if _env_truthy("LOCAL_MODEL_ENABLED") or _env_truthy("OPENAI_CLI_ENABLED"):
        configured.append("local")
    if _server_deepseek_api_key():
        configured.append("deepseek")

    # Also check user's stored keys if authenticated.
    if user is not None:
        try:
            if getattr(user, "anthropic_api_key", None):
                if "anthropic" not in configured:
                    configured.append("anthropic")
            api_keys = getattr(user, "api_keys", None) or {}
            if isinstance(api_keys, dict):
                for provider in ("anthropic", "openai", "deepseek"):
                    if api_keys.get(provider) and provider not in configured:
                        configured.append(provider)
        except Exception:
            pass

    selected_provider = str(api_provider or "").strip().lower() or (
        DEFAULT_AI_PROVIDER
        if DEFAULT_AI_PROVIDER in configured
        else (configured[0] if configured else DEFAULT_AI_PROVIDER)
    )
    selected_profile = resolve_model_profile(selected_provider, model_profile)
    profiles_by_id = all_model_profiles()
    default_models = {
        provider: profiles_by_id[profile_id].to_public_dict()
        for provider, profile_id in DEFAULT_MODEL_BY_PROVIDER.items()
        if profile_id in profiles_by_id
    }

    return {
        "configured_backends": configured,
        "needs_setup": len(configured) == 0,
        "available_models": available_model_profiles(),
        "default_model_by_provider": default_models,
        "selected_model_status": selected_profile.to_public_dict(),
    }


@router.post("/message", response_model=ChatResponse)
@limiter.limit("15/minute")
async def chat_message(
    request: Request,
    req: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the AI research agent.

    Uses Claude's native tool_use to call search/query/analysis tools,
    inspect results, and automatically plan next steps — a true agentic loop.
    Falls back to single-turn with <actions> tags if tool_use is unavailable.
    """
    provider_api_keys = _provider_api_keys(req.context, user)
    preferred_backend = _preferred_backend(req.context)
    preferred_model_profile = _preferred_model_profile(req.context)
    workflow_budget = _workflow_budget_config(_infer_workflow_budget_mode(req))

    claude_messages: list[dict] = _normalize_messages(req.messages)
    runtime = await _build_runtime(req, user, db)
    python_session_id = (req.context or {}).get("python_session_id", "default")
    chat_session_id = (req.context or {}).get("current_session_id")
    if not chat_session_id and python_session_id and python_session_id != "default":
        chat_session_id = str(python_session_id)
    _prime_adql_context_cache(req.context, python_session_id)
    await _prime_python_session_from_history(req.messages, python_session_id)

    try:
        response = await _run_orchestrated_chat(
            runtime=runtime,
            messages=claude_messages,
            provider_api_keys=provider_api_keys,
            python_session_id=python_session_id,
            preferred_backend=preferred_backend,
            model_profile=preferred_model_profile,
            user_id=str(user.id) if user else None,
            chat_session_id=chat_session_id,
            workflow_budget=workflow_budget,
        )
        return ChatResponse(reply=response["reply"], actions=response["actions"])

    except InferenceError as e:
        logger.error("Inference router error: %s", e)
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="The AI workflow took too long. Try a narrower query or split the task into separate query and analysis steps.",
        )
    except Exception as e:
        logger.exception("Unexpected AI chat failure")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected AI chat failure: {str(e) or e.__class__.__name__}",
        )


@router.post("/execute-action")
async def execute_action(
    action: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Execute an action suggested by the AI assistant."""
    import asyncio

    action_type = action.get("action")

    if action_type == "search":
        from app.connectors.registry import CONNECTORS_KEYS, get_connector
        from app.api.data import SearchResult, _astro_to_result, _resolve_search_coordinates
        from app.search.query_parser import parse_natural_query

        query = action.get("query", "")
        source_list = action.get("sources", ["sdss", "gaia", "simbad"])
        radius = action.get("radius", 0.1)

        # Filter out unknown sources
        source_list = [s for s in source_list if s in CONNECTORS_KEYS]
        if not source_list:
            source_list = ["simbad"]

        # Parse the natural language query to extract science criteria
        parsed = parse_natural_query(query)
        redshift_min = parsed.get("redshift_min")
        redshift_max = parsed.get("redshift_max")
        object_type = parsed.get("object_type")
        required_fields = parsed.get("required_fields", [])
        has_science_criteria = any(
            [redshift_min, redshift_max, object_type, required_fields]
        )

        search_ra = None
        search_dec = None
        resolved_name = None
        candidate_name = query.strip()
        if candidate_name:
            search_ra, search_dec = await _resolve_search_coordinates(candidate_name, None, None)
            if search_ra is not None and search_dec is not None:
                resolved_name = candidate_name

        async def _search_one(source: str):
            connector = get_connector(source)
            # Use SIMBAD's criteria-based TAP search for science queries
            if (
                source == "simbad"
                and has_science_criteria
                and hasattr(connector, "search_by_criteria")
            ):
                return await asyncio.wait_for(
                    connector.search_by_criteria(
                        object_type=object_type,
                        redshift_min=redshift_min,
                        redshift_max=redshift_max,
                        ra=search_ra,
                        dec=search_dec,
                        radius=radius,
                        required_fields=required_fields,
                    ),
                    timeout=45.0,
                )
            # For coordinate-based connectors, skip if we have no coordinates
            # and the query is a science description (not a resolvable name)
            if search_ra is None and not resolved_name:
                return []
            search_q = resolved_name or query
            return await asyncio.wait_for(
                connector.search(search_q, ra=search_ra, dec=search_dec, radius=radius),
                timeout=45.0,
            )

        tasks = [_search_one(s) for s in source_list]
        results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: list[SearchResult] = []
        for source_name, result in zip(source_list, results_per_source):
            if isinstance(result, Exception):
                logger.warning("Chat search failed for %s: %s", source_name, result)
                all_results.append(
                    SearchResult(
                        source=source_name,
                        object_id="error",
                        name=f"Error querying {source_name}: {result}",
                        ra=0,
                        dec=0,
                        error_type="connection",
                    )
                )
                continue
            all_results.extend(_astro_to_result(obj) for obj in result)

        # If no science-based connectors were in the list, add SIMBAD automatically
        if has_science_criteria and "simbad" not in source_list:
            try:
                simbad = get_connector("simbad")
                extra = await asyncio.wait_for(
                    simbad.search_by_criteria(
                        object_type=object_type,
                        redshift_min=redshift_min,
                        redshift_max=redshift_max,
                        ra=search_ra,
                        dec=search_dec,
                        radius=radius,
                        required_fields=required_fields,
                    ),
                    timeout=45.0,
                )
                all_results.extend(_astro_to_result(obj) for obj in extra)
            except Exception as e:
                logger.warning("Chat fallback SIMBAD search failed: %s", e)

        return {"type": "search_results", "data": [r.model_dump() for r in all_results]}

    elif action_type == "adql":
        # Call the integration endpoint directly (no rate limiter on this one)
        from app.api.integration import adql_query, ADQLRequest

        req = ADQLRequest(
            query=action.get("query", ""), service=action.get("service", "gaia")
        )
        result = await adql_query(req)
        return {"type": "adql_results", "data": result}

    elif action_type == "plot":
        from app.pipeline.nodes.plot_interactive import build_chart

        chart_type = action.get("chart_type", "correlation_scatter")
        data = action.get("data", {})
        params = action.get("params", {})
        plot_json = build_chart(chart_type, data, params)
        return {"type": "plot", "data": plot_json}

    elif action_type == "arxiv":
        from app.api.arxiv import extract_arxiv_tables, ArxivTableRequest

        arxiv_id = action.get("arxiv_id", "")
        result = await extract_arxiv_tables(ArxivTableRequest(arxiv_id=arxiv_id))
        return {"type": "arxiv_tables", "data": result.model_dump()}

    elif action_type == "run_pipeline":
        from app.api.pipeline import run_pipeline, RunRequest
        from starlette.requests import Request as StarletteRequest

        nodes = action.get("nodes", [])
        input_data_id = action.get("input_data_id", "")
        dag = {"nodes": nodes, "edges": action.get("edges", [])}
        req = RunRequest(dag=dag, input_data_id=input_data_id)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/chat/execute-action",
            "headers": [],
            "query_string": b"async_mode=false",
        }
        mock_request = StarletteRequest(scope)
        result = await run_pipeline(
            request=mock_request, req=req, db=db, user=user, async_mode=False
        )
        return {"type": "pipeline_result", "data": result.model_dump()}

    elif action_type == "generate_pipeline":
        # AI generated a pipeline DAG — validate and return it for the frontend to load
        name = action.get("name", "AI-Generated Pipeline")
        description = action.get("description", "")
        dag = action.get("dag", {})

        # Validate DAG structure
        if "nodes" not in dag or "edges" not in dag:
            raise HTTPException(
                status_code=400, detail="Generated DAG must have 'nodes' and 'edges'"
            )

        from app.pipeline.nodes import registry as node_registry

        valid_types = set(node_registry.keys())

        # Auto-assign positions if missing
        for i, node in enumerate(dag.get("nodes", [])):
            if "position" not in node:
                node["position"] = {"x": i * 300, "y": 150}
            if "data" not in node:
                node["data"] = {"label": node.get("type", ""), "params": {}}
            elif "label" not in node["data"]:
                node["data"]["label"] = node.get("type", "")

        # Warn about unknown node types but don't reject
        warnings = []
        for node in dag.get("nodes", []):
            if node.get("type") not in valid_types:
                warnings.append(f"Unknown node type: {node.get('type')}")

        # Optionally save as template
        if user:
            from app.models.schemas import PipelineTemplateDB

            tpl = PipelineTemplateDB(
                name=name,
                description=description,
                dag=dag,
                user_id=user.id,
            )
            db.add(tpl)
            await db.commit()
            await db.refresh(tpl)
            template_id = str(tpl.id)
        else:
            template_id = None

        return {
            "type": "generated_pipeline",
            "data": {
                "name": name,
                "description": description,
                "dag": dag,
                "template_id": template_id,
                "warnings": warnings,
            },
        }

    elif action_type == "modify_pipeline":
        # AI wants to modify an existing pipeline
        modifications = action.get("modifications", [])
        explanation = action.get("explanation", "")
        current_dag = action.get("current_dag")

        # If no current_dag provided via context, try to get from context
        if not current_dag and (req_context := action.get("context")):
            current_dag = req_context.get("current_dag")

        return {
            "type": "pipeline_modification",
            "data": {
                "modifications": modifications,
                "explanation": explanation,
                "current_dag": current_dag,
            },
        }

    elif action_type == "comment_pipeline":
        template_id = action.get("template_id", "")
        comment_text = action.get("comment", "")

        if template_id and user:
            from app.models.schemas import PipelineComment

            try:
                tid = uuid.UUID(template_id)
                comment = PipelineComment(
                    template_id=tid,
                    user_id=user.id,
                    content=f"[AI Review] {comment_text}",
                )
                db.add(comment)
                await db.commit()
            except (ValueError, Exception) as e:
                logger.warning(f"Failed to save pipeline comment: {e}")

        return {
            "type": "pipeline_comment",
            "data": {
                "template_id": template_id,
                "comment": comment_text,
            },
        }

    elif action_type == "explain":
        return {"type": "explanation", "data": {"topic": action.get("topic", "")}}

    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown action type: {action_type}"
        )


# ── Chat Session Persistence ──


class SaveSessionRequest(BaseModel):
    session_id: str | None = None
    title: str | None = None
    messages: list[dict]
    # R7: optional audit trail uploaded by the frontend.  Captures the
    # thinking-stream events (agent_text / tool_call / tool_result) for
    # the session so we can reconstruct "what did the AI actually do?"
    audit_log: list[dict] | None = None


class RenameSessionRequest(BaseModel):
    title: str


def _auto_title_from_messages(messages: list[dict]) -> str:
    """Generate a concise title from the first user message."""
    for m in messages:
        if m.get("role") == "user":
            raw = str(m.get("content", "")).strip()
            # Use first line, truncated to 60 chars
            first_line = raw.split("\n", 1)[0].strip()
            return (first_line[:60] or "New Chat")
    return "New Chat"


class SessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    updated_at: str


@router.post("/sessions/save")
async def save_chat_session(
    req: SaveSessionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save or update a chat session.

    R11: respects the ``Idempotency-Key`` header.  If we've already
    executed this key for this user, we return the cached response
    without re-running the save — safe for accidental retries / network
    flakes that lead the frontend to post twice.
    """
    from app.models.schemas import ChatSession
    from sqlalchemy import select
    from app.services.memory_service import memory_service
    from app.services import idempotency as _idemp

    idempotency_key = request.headers.get("idempotency-key")
    if idempotency_key:
        cached = await _idemp.lookup(db, idempotency_key, str(user.id))
        if cached is not None:
            return cached

    if req.session_id:
        try:
            sid = uuid.UUID(req.session_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid session ID")
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == sid, ChatSession.user_id == user.id
            )
        )
        session = result.scalar_one_or_none()
        if session:
            session.messages = req.messages
            if req.audit_log is not None:
                session.audit_log = req.audit_log  # R7
            # Only update title if explicitly provided AND not empty.
            # Don't overwrite a meaningful title with "New Chat" on auto-save.
            if req.title and req.title.strip() and req.title != "New Chat":
                session.title = req.title
            elif session.title == "New Chat" and req.messages:
                # If session still has default title, auto-generate from first message
                session.title = _auto_title_from_messages(req.messages)
            session.updated_at = datetime.now(timezone.utc)
            # R13: wrap memory refresh in try/except so its failure does not
            # abort the message save.  Partial success is preferable to the
            # user silently losing their chat because memory service hiccuped.
            try:
                await memory_service.refresh_session_memory(user.id, session.id, db)
            except Exception as mem_exc:
                logger.warning("memory_service.refresh_session_memory failed: %s", mem_exc)
            await db.commit()
            response = {"id": str(session.id), "saved": True, "title": session.title}
            if idempotency_key:
                try:
                    await _idemp.store(db, idempotency_key, str(user.id), response)
                except Exception as exc:
                    logger.debug("Idempotency store failed: %s", exc)
            return response

    # Create new session — auto-generate title from first user message if not provided
    title = req.title if (req.title and req.title.strip() and req.title != "New Chat") else _auto_title_from_messages(req.messages)

    session = ChatSession(
        user_id=user.id,
        title=title,
        messages=req.messages,
        audit_log=req.audit_log,
    )
    db.add(session)
    await db.flush()
    try:
        await memory_service.refresh_session_memory(user.id, session.id, db)
    except Exception as mem_exc:
        logger.warning("memory_service.refresh_session_memory failed: %s", mem_exc)
    await db.commit()
    await db.refresh(session)
    response = {"id": str(session.id), "saved": True, "title": session.title}
    if idempotency_key:
        try:
            await _idemp.store(db, idempotency_key, str(user.id), response)
        except Exception as exc:
            logger.debug("Idempotency store failed: %s", exc)
    return response


@router.patch("/sessions/{session_id}")
async def rename_chat_session(
    session_id: str,
    req: RenameSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Rename a chat session."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select
    from datetime import datetime, timezone

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if len(title) > 200:
        title = title[:200]

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == sid, ChatSession.user_id == user.id
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    session.title = title
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": str(session.id), "title": title}


@router.get("/sessions", response_model=list[SessionSummary])
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List user's saved chat sessions."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select

    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
    )
    sessions = result.scalars().all()
    return [
        SessionSummary(
            id=str(s.id),
            title=s.title,
            message_count=len(s.messages) if isinstance(s.messages, list) else 0,
            updated_at=s.updated_at.isoformat() if s.updated_at else "",
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Load a saved chat session."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid, ChatSession.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "id": str(session.id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a chat session."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid, ChatSession.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.delete(session)
    await db.commit()
    return {"deleted": True}


@router.post("/sessions/import")
async def import_chat_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Import a chat session from a JSON file."""
    from app.models.schemas import ChatSession
    from app.services.memory_service import memory_service

    body = await request.json()

    # Validate structure
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="Invalid format: 'messages' must be a list")

    # Validate each message has required fields
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid message at index {i}: must have 'role' and 'content'",
            )

    title = body.get("title", "Imported Session")
    # Auto-title from first user message when no title is provided
    if title == "Imported Session" and messages:
        for m in messages:
            if m.get("role") == "user":
                title = m["content"][:60]
                break

    session = ChatSession(
        user_id=user.id,
        title=title,
        messages=messages,
    )
    db.add(session)
    await db.flush()
    await memory_service.refresh_session_memory(user.id, session.id, db)
    await db.commit()
    await db.refresh(session)

    return {
        "id": str(session.id),
        "title": session.title,
        "message_count": len(messages),
    }
