"""AI assistant backed by the inference router and multi-agent orchestrator."""

import asyncio
import contextlib
import inspect
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.inference_router import InferenceError
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
from app.rate_limit import daily_quota, get_client_ip, limiter
from app.models.database import get_db
from app.models.schemas import User
from app.services.prompt_loader import build_allowed_tools, build_system_prompt
from app.services.agent_session_state import update_session_status as _update_chat_session_status

# ── Agent runtime re-exports (2026-07-03 god-file split) ───────────────
#
# The non-router agent runtime moved to app/services/agent_runtime/.
# Every moved symbol is re-imported here so the existing public import
# surface of app.api.chat (26 test files, scripts, app.api.config) keeps
# working, and so the chat module remains the monkeypatch seam the tests
# rely on: the agent loop resolves _llm_messages_create /
# _execute_tool_calls / _filter_tools_by_research_focus /
# _ASTRO_RESEARCH_FOCUS through THIS module at call time (see
# app/services/agent_runtime/loop.py shims).
from app.services.agent_runtime.approval import APPROVAL_STATE_NONE
from app.services.agent_runtime.approval import mark_unapproved_claims
from app.services.agent_runtime.runtime_config import _DEFAULT_WORKFLOW_BUDGET  # noqa: F401
from app.services.agent_runtime.runtime_config import _LONG_WORKFLOW_BUDGET  # noqa: F401
from app.services.agent_runtime.runtime_config import _workflow_budget_config  # noqa: F401
from app.services.agent_runtime.runtime_config import _is_tool_inventory_request  # noqa: F401

from app.services.agent_runtime.sse import _SSE_FIELD_BIBCODES_PER_COLUMN  # noqa: F401
from app.services.agent_runtime.sse import _bounded_provenance_for_sse  # noqa: F401
from app.services.agent_runtime.sse import _slim_tool_result_for_sse  # noqa: F401
from app.services.agent_runtime.sse import _trim_large_tool_results  # noqa: F401

from app.services.agent_runtime.tool_execution import _checkpoint_session_id  # noqa: F401
from app.services.agent_runtime.tool_execution import _hash_tool_input  # noqa: F401
from app.services.agent_runtime.tool_execution import _checkpoint_cache_refs  # noqa: F401
from app.services.agent_runtime.tool_execution import _checkpoint_status  # noqa: F401
from app.services.agent_runtime.tool_execution import _checkpoint_result_summary  # noqa: F401
from app.services.agent_runtime.tool_execution import _record_tool_checkpoint  # noqa: F401
from app.services.agent_runtime.tool_execution import _format_checkpoint_resume_note  # noqa: F401
from app.services.agent_runtime.tool_execution import _LAST_PROMPT_DEBUG  # noqa: F401
from app.services.agent_runtime.tool_execution import _llm_messages_create  # noqa: F401
from app.services.agent_runtime.tool_execution import _execute_tool_calls  # noqa: F401
from app.services.agent_runtime.tool_execution import _tool_results_to_actions  # noqa: F401
from app.services.agent_runtime.tool_execution import _tool_results_from_stream_audit  # noqa: F401

from app.services.agent_runtime.abstention import _parse_actions  # noqa: F401
from app.services.agent_runtime.abstention import _strip_actions_from_reply  # noqa: F401
from app.services.agent_runtime.abstention import _ABSTENTION_RE  # noqa: F401
from app.services.agent_runtime.abstention import _ATTR_RE  # noqa: F401
from app.services.agent_runtime.abstention import _REAL_CACHE_READER_CALL_NAMES  # noqa: F401
from app.services.agent_runtime.abstention import _REAL_CACHE_READER_CALL_CHAINS  # noqa: F401
from app.services.agent_runtime.abstention import _REAL_CACHE_READER_MODULES  # noqa: F401
from app.services.agent_runtime.abstention import _run_python_code_reads_real_cache  # noqa: F401
from app.services.agent_runtime.abstention import _TRUNCATED_TRAILING_PUNCT  # noqa: F401
from app.services.agent_runtime.abstention import _TRUNCATED_TRAILING_CONNECTIVES  # noqa: F401
from app.services.agent_runtime.abstention import _reply_looks_truncated  # noqa: F401
from app.services.agent_runtime.abstention import _parse_abstention_tag  # noqa: F401
from app.services.agent_runtime.abstention import _normalize_abstention_attr_key  # noqa: F401
from app.services.agent_runtime.abstention import _classify_abstention_reason  # noqa: F401
from app.services.agent_runtime.abstention import _sequence_or_mapping_is_empty  # noqa: F401
from app.services.agent_runtime.abstention import _is_failed_or_empty_data_fetch  # noqa: F401
from app.services.agent_runtime.abstention import _user_requested_synthetic_demo  # noqa: F401
from app.services.agent_runtime.abstention import _abstention_attrs_without_numeric_claims  # noqa: F401
from app.services.agent_runtime.abstention import _render_abstention_card  # noqa: F401
from app.services.agent_runtime.abstention import _sanitize_tools_returned_nothing  # noqa: F401

from app.services.agent_runtime.summaries import _tool_grounded_timeout_summary  # noqa: F401
from app.services.agent_runtime.summaries import _format_dataset_gap_item  # noqa: F401
from app.services.agent_runtime.summaries import _line_measurement_count_from_result  # noqa: F401
from app.services.agent_runtime.summaries import _line_fit_publication_ready_from_result  # noqa: F401
from app.services.agent_runtime.summaries import _line_fit_partial_from_result  # noqa: F401
from app.services.agent_runtime.summaries import _fmt_tool_number  # noqa: F401
from app.services.agent_runtime.summaries import _line_lfr_tool_grounded_summary  # noqa: F401
from app.services.agent_runtime.summaries import _statistics_tool_grounded_summary  # noqa: F401
from app.services.agent_runtime.summaries import _cosmology_requested_redshift  # noqa: F401
from app.services.agent_runtime.summaries import _cosmology_max_z_coverage  # noqa: F401
from app.services.agent_runtime.summaries import _cosmology_tool_grounded_summary  # noqa: F401
from app.services.agent_runtime.summaries import _enforce_cosmology_dataset_identity  # noqa: F401
from app.services.agent_runtime.summaries import _research_tool_grounded_summary  # noqa: F401
from app.services.agent_runtime.summaries import _successful_research_report_export  # noqa: F401
from app.services.agent_runtime.summaries import _tool_grounded_summary  # noqa: F401

from app.services.agent_runtime.prompt_routing import _cosmology_dataset_keys_present  # noqa: F401
from app.services.agent_runtime.prompt_routing import _COSMOLOGY_ANCHOR_NUMERIC_PATTERNS  # noqa: F401
from app.services.agent_runtime.prompt_routing import _ANCHOR_PARAM_TOKEN_RE  # noqa: F401
from app.services.agent_runtime.prompt_routing import _ANCHOR_NUMBER_RE  # noqa: F401
from app.services.agent_runtime.prompt_routing import _unsupported_cosmology_anchor_numeric_comparison  # noqa: F401
from app.services.agent_runtime.prompt_routing import _NUMBER_RE  # noqa: F401
from app.services.agent_runtime.prompt_routing import _parse_inline_numeric_array  # noqa: F401
from app.services.agent_runtime.prompt_routing import _parse_inline_uniform_error  # noqa: F401
from app.services.agent_runtime.prompt_routing import _inline_statistics_tool_call_from_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _is_cosmology_likelihood_workflow  # noqa: F401
from app.services.agent_runtime.prompt_routing import _is_research_program_workflow  # noqa: F401
from app.services.agent_runtime.prompt_routing import _research_plan_from_tool_results  # noqa: F401
from app.services.agent_runtime.prompt_routing import _research_evidence_graph_from_tool_results  # noqa: F401
from app.services.agent_runtime.prompt_routing import _compact_tool_results_for_evidence  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_prompt_mentions_act  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_prompt_mentions_spt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_prompt_forbids_family  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_forbidden_probe_families  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_probe_family_for_dataset  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_prompt_mentions_bao  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_prompt_mentions_weak_lensing  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_dataset_keys_from_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_supernova_sets_from_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_models_from_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _should_build_cosmology_robustness_matrix  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_likelihood_build_calls_from_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_direct_route_from_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_likelihood_run_calls_from_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_likelihood_executable_only_prompt  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_requires_dedicated_spectra_likelihood  # noqa: F401
from app.services.agent_runtime.prompt_routing import _cosmology_has_dedicated_model_gap  # noqa: F401

from app.services.agent_runtime.line_relation import _line_fit_method_from_prompt  # noqa: F401
from app.services.agent_runtime.line_relation import _line_fit_cosmology_from_prompt  # noqa: F401
from app.services.agent_runtime.line_relation import _line_fit_subsample_splits_from_prompt  # noqa: F401
from app.services.agent_runtime.line_relation import _line_fit_context  # noqa: F401
from app.services.agent_runtime.line_relation import _is_line_relation_workflow  # noqa: F401
from app.services.agent_runtime.line_relation import _extract_arxiv_id_from_paper  # noqa: F401
from app.services.agent_runtime.line_relation import _rank_literature_candidate_for_line_lfr  # noqa: F401
from app.services.agent_runtime.line_relation import _ranked_literature_arxiv_candidates  # noqa: F401
from app.services.agent_runtime.line_relation import _verified_line_relation_seed_candidates  # noqa: F401
from app.services.agent_runtime.line_relation import _literature_arxiv_candidates  # noqa: F401
from app.services.agent_runtime.line_relation import _table_extraction_arxiv_ids  # noqa: F401
from app.services.agent_runtime.line_relation import _run_python_reads_real_cache  # noqa: F401
from app.services.agent_runtime.line_relation import _should_suppress_line_measurement_synthetic_python  # noqa: F401
from app.services.agent_runtime.line_relation import _suppressed_line_measurement_python_result  # noqa: F401
from app.services.agent_runtime.line_relation import _suppressed_line_relation_search_result  # noqa: F401
from app.services.agent_runtime.line_relation import _suppressed_line_relation_extract_result  # noqa: F401

from app.services.agent_runtime.loop import _run_agent_loop  # noqa: F401

# M6 gate wiring surface: the chat validation pipeline invokes
# methodology_consistency_violations inside _run_agent_loop (moved to
# app/services/agent_runtime/loop.py with the rest of the gate stack).
# Re-exported here so app.api.chat remains the documented import point
# for the reply-gate helpers (pinned by test_m6_methodology_validator).
from app.services.claim_validator import methodology_consistency_violations  # noqa: F401

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
# (3) write the tools list in modules/<name>/manifest.yaml.
_FOCUS_GATED_VALUES = frozenset({"cosmology"})


def _filter_tools_by_research_focus(tools: list[dict]) -> list[dict]:
    """L1 hard tool gating per ASTRO_RESEARCH_FOCUS env.

    ``all`` is the explicit escape hatch that exposes every tool (admin/debug).
    A gated focus in ``_FOCUS_GATED_VALUES`` uses its own manifest allowlist.
    Any other value — empty, a typo, or a stale pin of an extracted module
    (e.g. ``solar_system`` / ``exoplanet`` after they moved to
    standard-astro-verticals on 2026-06-03) — fails CLOSED to the default
    cosmology allowlist rather than silently exposing the full tool surface
    (incl. retained dormant tools) under a cosmology-only system prompt.
    """
    if _ASTRO_RESEARCH_FOCUS == "all":
        filtered = tools
    else:
        focus = _ASTRO_RESEARCH_FOCUS if _ASTRO_RESEARCH_FOCUS in _FOCUS_GATED_VALUES else "cosmology"
        allowed = build_allowed_tools(focus)
        filtered = [t for t in tools if t.get("name") in allowed]
    # A disabled v0.2 flag must restore the historical wire-visible surface,
    # not merely make the executor fail after the model has already selected it.
    from app.config import settings

    if not settings.lightweight_verification_enabled:
        return [
            tool
            for tool in filtered
            if tool.get("name") != "verify_scalar_derivation"
        ]
    return filtered


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
    # M7 follow-through (audit 2026-07-03): true when the agent loop exhausted
    # its iteration budget while the model still wanted to continue — the
    # reply is a truncated multi-step workflow, not a complete answer.
    hit_iteration_cap: bool = False
    # 2026-07-03 honesty surfacing: compact per-reply summary of what the
    # validation gate stack did (numeric_gate / citation_gate /
    # regen_count / interventions), derived in the agent loop from state
    # the gates already computed.  Optional and backward compatible —
    # old clients ignore it, old replies simply lack it.
    validation_summary: dict | None = None


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
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _server_deepseek_api_key() -> str:
    """Server-funded DeepSeek key for public/anonymous chat.

    Anthropic/OpenAI remain BYOK. DeepSeek is the only supported hosted shared
    provider, but it is disabled unless the operator explicitly opts in.
    """

    env_flag = os.getenv("SHARED_DEEPSEEK_API_KEY_ENABLED")
    try:
        from app.config import settings

        settings_enabled = bool(getattr(settings, "shared_deepseek_api_key_enabled", False))
        settings_key = (
            str(getattr(settings, "platform_deepseek_api_key", "") or "").strip()
            or str(getattr(settings, "deepseek_api_key", "") or "").strip()
        )
    except Exception:
        settings_enabled = False
        settings_key = ""
    if env_flag is not None:
        if not _env_flag_enabled("SHARED_DEEPSEEK_API_KEY_ENABLED", default=False):
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
    # A user-selected BYOK/local route must never fall back to the platform
    # DeepSeek key after their backend fails. Otherwise an invalid BYOK can
    # obtain quota-exempt, platform-funded inference through the router's
    # fallback chain. Retain user-owned cross-provider keys: those fallbacks
    # are still paid by the user.
    selected_uses_own_backend = (
        context_provider in {"anthropic", "openai", "deepseek"}
        and bool(keys.get(context_provider))
    ) or (context_provider == "local" and _local_backend_configured())
    if "deepseek" not in keys and not selected_uses_own_backend:
        server_key = _server_deepseek_api_key()
        if server_key:
            keys["deepseek"] = server_key
    # Anthropic/OpenAI never fall back to platform env keys: they remain BYOK.
    return keys


def _enforce_starter_daily_quota(
    user: User | None,
    context: dict | None,
    provider_api_keys: dict[str, str],
    *,
    request: Request | None = None,
) -> None:
    """Reject over-quota platform-funded chat before inference or SSE starts.

    Decision 2B (2026-07): self-service signups land on the "starter" tier
    (app/rate_limit.py TIER_LIMITS) so a fresh registration cannot burn the
    shared server DeepSeek key — the only platform-funded provider;
    Anthropic/OpenAI stay BYOK (see _provider_api_keys). Only calls that
    would run on the platform key count against the cap:

    - The user's own DeepSeek key shadows the platform key entirely in
      _provider_api_keys -> exempt, not counted.
    - An explicitly selected provider backed by the user's own key runs on
      their money -> exempt. _provider_api_keys deliberately withholds the
      platform key in this case, so a failed or invalid BYOK cannot turn into
      an uncharged platform-funded fallback.
    - Everything else counts (fail closed). In particular, a provider
      preference WITHOUT a matching user key is counted, because the
      router's fallback chain would route it straight back to the platform
      DeepSeek key.

    Anonymous shared-key calls use the same 50/day cap, keyed by the client IP
    derived through ``get_client_ip``'s trusted-proxy policy. Pre-existing
    tiers (solo/lab/institution) are intentionally not enforced here.

    Hosted shared-key traffic requires the Redis-backed counter. A Redis
    outage fails closed with 503 instead of silently resetting the daily
    allowance in each web process. Local development can use the in-memory
    fallback so contributors do not need Redis just to exercise chat.
    """
    server_key = _server_deepseek_api_key()
    if not server_key:
        return  # no platform-funded key configured -> nothing to protect
    effective_deepseek_key = provider_api_keys.get("deepseek")
    if effective_deepseek_key and effective_deepseek_key != server_key:
        return  # the user's own DeepSeek key is in effect
    own_key_providers = {
        provider
        for provider, key in provider_api_keys.items()
        if provider in {"anthropic", "openai", "deepseek"}
        and key
        and not (provider == "deepseek" and key == server_key)
    }
    preferred = str((context or {}).get("api_provider") or "").strip().lower()
    if preferred and preferred in own_key_providers:
        return
    if preferred == "local" and _local_backend_configured():
        return  # dev-only local backend spends no platform money

    if user is None:
        if request is None:
            raise HTTPException(
                status_code=503,
                detail="Anonymous chat cannot verify its daily quota identity",
            )
        client_ip = get_client_ip(request)
        if not client_ip:
            raise HTTPException(
                status_code=503,
                detail="Anonymous chat cannot verify its daily quota identity",
            )
        quota_subject = f"anonymous-ip:{client_ip}"
        quota_tier = "anonymous"
    else:
        if (user.subscription_tier or "solo") != "starter":
            return
        quota_subject = str(user.id)
        quota_tier = "starter"

    runtime_env = os.getenv("ENV", "dev").strip().lower()
    require_durable = runtime_env in {"prod", "production"} or _env_flag_enabled(
        "RENDER", default=False
    )
    verdict = daily_quota.check_and_increment(
        quota_subject,
        quota_tier,
        "api_calls",
        require_durable=require_durable,
    )
    if verdict.get("backend_unavailable"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Platform-funded chat is temporarily unavailable because its "
                "daily quota service cannot be verified. Try again later or "
                "use your own API key."
            ),
        )
    if not verdict.get("allowed", True):
        limit = verdict.get("limit", 50)
        subject = "Anonymous access includes" if user is None else "New accounts get"
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily limit reached: {subject} {limit} platform-funded "
                f"chat messages per day, and you have used all {limit}. The "
                "counter resets at midnight UTC. To keep working now, add your "
                "own API key (Anthropic, OpenAI, or DeepSeek) under Settings "
                "-> API Keys — messages that run on your own key do not count "
                "against this limit."
            ),
        )


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


def _subscription_cli_enabled(name: str) -> bool:
    """Return whether a local subscription CLI is safely available here."""
    environment = os.getenv("ENV", "dev").strip().lower()
    return environment not in {"production", "prod"} and _env_truthy(name)


def _local_backend_configured() -> bool:
    """Whether the local provider has an actually usable configured backend."""
    return (
        _env_truthy("LOCAL_MODEL_ENABLED")
        or _subscription_cli_enabled("OPENAI_CLI_ENABLED")
        or _subscription_cli_enabled("CLAUDE_CLI_ENABLED")
    )


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


def _debug_stream_enabled(request: Request | None, context: dict | None) -> bool:
    if request is not None and request.query_params.get("debug_stream") == "1":
        return True
    return _context_truthy((context or {}).get("debug_stream"))


def _filter_tools(tool_names: list[str] | None, tools: list[dict]) -> list[dict]:
    if not tool_names:
        return tools
    allowed = set(tool_names)
    selected = [tool for tool in tools if tool["name"] in allowed]
    return selected or tools


async def _build_runtime(
    req: ChatRequest,
    user: User | None,
    db: AsyncSession,
):
    from app.config import settings
    from app.services.ai_tools import TOOLS

    available_tools = (
        [tool for tool in TOOLS if tool.get("name") != "run_python"]
        if settings.sandbox_backend == "disabled"
        else TOOLS
    )

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
    toolset = available_tools
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
        toolset = (
            available_tools
            if _is_tool_inventory_request(latest_user_message)
            else _filter_tools(runtime.get("tool_names"), available_tools)
        )
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


async def _validated_current_session_id(
    context: dict[str, Any] | None,
    user: User | None,
    db: AsyncSession,
) -> str | None:
    """Resolve a client-supplied chat session inside the caller's account.

    ``current_session_id`` controls durable checkpoints, tool provenance, and
    the session's running/idle marker.  Treating it as an opaque client string
    lets one account write another account's session state.  Resolve it once at
    the HTTP boundary and pass only the owned database id downstream.

    Anonymous chat remains available when no durable session is requested.  A
    supplied session id, however, fails closed because there is no owner
    identity against which it can be authorized.
    """
    if not isinstance(context, dict):
        return None
    raw_session_id = context.get("current_session_id")
    if raw_session_id is None or not str(raw_session_id).strip():
        return None
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication is required to use a saved chat session",
        )
    try:
        session_id = uuid.UUID(str(raw_session_id).strip())
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid chat session ID") from exc

    from sqlalchemy import select

    from app.models.schemas import ChatSession

    owned_id = (
        await db.execute(
            select(ChatSession.id).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if owned_id is None:
        # Do not reveal whether the id exists in another account.
        raise HTTPException(status_code=404, detail="Chat session not found")
    return str(owned_id)


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

    chat_session_id = await _validated_current_session_id(req.context, user, db)
    owner_id = str(user.id) if user is not None else None
    anonymous_job_scope = f"anonymous:{uuid.uuid4().hex}" if user is None else None

    # Reject over-quota platform-funded starter/anonymous calls before the
    # stream opens, so the client gets a clean HTTP 429 (or quota-backend 503)
    # instead of an in-stream error frame.
    _enforce_starter_daily_quota(
        user,
        req.context,
        _provider_api_keys(req.context, user),
        request=request,
    )

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

        from app.services.ai_tools import build_trusted_python_session_id

        python_session_id = build_trusted_python_session_id(
            user_id=owner_id,
            chat_session_id=chat_session_id,
            requested_session_id=(req.context or {}).get(
                "python_session_id", "default"
            ),
            anonymous_scope=anonymous_job_scope,
        )
        if owner_id:
            await _register_active_python_session(owner_id, python_session_id, db)
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
            chat_session_id,
            owner_id=owner_id,
            status="running",
            current_run_id=_agent_run_id,
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
            # Compact copy of every thinking-stream event, kept for the
            # duration of this request only.
            #
            # Corrected 2026-09-03 (adversarial review): the original R7 note
            # here said this list is persisted to ChatSession.audit_log, and
            # that claim was reused downstream to argue the streamed drafts
            # were durable.  It is not true.  `audit_trail` is request-local
            # and has exactly one consumer — `_tool_results_from_stream_audit`
            # in the workflow-timeout fallback below — while
            # ChatSession.audit_log holds server-owned HMAC-signed evidence
            # records (app/services/server_evidence.py) and the
            # client-supplied field is ignored on save.  Streamed events are
            # still gated before they leave the process, but the reason is
            # that they are visible live and land in the blind runner's
            # case_<id>.json, not that a database row keeps them.  Raw
            # tool_result payloads may be huge; only a shallow preview is
            # kept, with the full result staying in the actions list.
            audit_trail: list[dict] = []

            async def _emit(evt: dict) -> None:
                # Keep a capped copy for the timeout fallback (see above).
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
                # information at all. Diagnostic fields are preserved and only the
                # large-volume fields (rows / data / figures / variables / stdout)
                # are replaced with preview/offloaded markers.
                #
                # Audit 2026-07-03: this block used to be an inline COPY of
                # _slim_tool_result_for_sse; the copies drifted (this one
                # dropped the provenance block).  Both wire paths now share
                # the module-level function.
                if evt.get("type") == "tool_result":
                    slimmed_result = _slim_tool_result_for_sse(evt.get("result"))
                    if slimmed_result is not evt.get("result"):
                        evt = dict(evt)
                        evt["result"] = slimmed_result
                await event_queue.put(evt)

            from app.services.async_tool_runtime import anonymous_owner_scope

            # create_task copies contextvars. Bind a request-unique anonymous
            # scope only while creating the orchestrator task so every async
            # submit/poll in this stream shares it, while another anonymous
            # request cannot deduplicate or read these jobs.
            with anonymous_owner_scope(anonymous_job_scope):
                work_task = asyncio.create_task(
                    asyncio.wait_for(
                        _run_orchestrated_chat(
                            runtime=runtime,
                            messages=claude_messages,
                            provider_api_keys=provider_api_keys,
                            python_session_id=python_session_id,
                            preferred_backend=preferred_backend,
                            model_profile=preferred_model_profile,
                            user_id=owner_id,
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

            # Publication evidence is written by the server from the actual
            # orchestrator result.  The browser transcript is display state
            # and is never trusted as proof that a tool ran.
            from app.services.server_evidence import append_server_evidence

            await append_server_evidence(
                session_id=chat_session_id,
                owner_id=owner_id,
                run_id=_agent_run_id,
                assistant_reply=str(response.get("reply") or ""),
                tool_results=(
                    response.get("tool_results")
                    if isinstance(response.get("tool_results"), list)
                    else []
                ),
                validation_summary=(
                    response.get("validation_summary")
                    if isinstance(response.get("validation_summary"), dict)
                    else None
                ),
            )

            if response["reply"]:
                # M7 follow-through (audit 2026-07-03): hit_iteration_cap was
                # computed "so the UI can surface it" and then dropped at this
                # boundary — thread it onto the final text frame so a truncated
                # multi-step workflow is distinguishable from a complete answer.
                # Same pattern for validation_summary (2026-07-03 honesty
                # surfacing): the gate stack's per-reply outcome rides the
                # final text frame so the UI can render a validation badge.
                yield f"data: {json.dumps({'type': 'text', 'content': response['reply'], 'hit_iteration_cap': bool(response.get('hit_iteration_cap', False)), 'validation_summary': response.get('validation_summary')})}\n\n"
            # Keep emitting the final consolidated tool_result events too —
            # downstream clients that only know the old protocol still work,
            # and the live-stream tool_result events above carry a __preview__
            # only, so the final ones deliver the full actions list.
            for action in response["actions"]:
                yield f"data: {json.dumps({'type': 'tool_result', 'tool': action.get('action'), 'result': _slim_tool_result_for_sse(action.get('tool_result')), 'tool_call_id': action.get('_tool_call_id')}, default=str)}\n\n"
        except (TimeoutError, asyncio.TimeoutError):
            _run_failed = True
            timeout_s = int(workflow_budget["endpoint_timeout_seconds"])
            timeout_tool_results = _tool_results_from_stream_audit(
                audit_trail if "audit_trail" in locals() else []
            )
            timeout_summary = _tool_grounded_timeout_summary(timeout_tool_results, timeout_s)
            timeout_validation_summary = {
                "schema_version": 1,
                # Chat has no approval state; this path says so as well, or
                # the badge cannot state it for the one reply with the least
                # review work behind it (Codex review 2026-09-03,
                # PRRT_kwDORoeoE86ethcX).
                "approval_state": APPROVAL_STATE_NONE,
                "numeric_gate": "not_run",
                "citation_gate": "not_run",
                "blocked": False,
                "reason": "workflow_timeout_tool_grounded_fallback",
            }
            if timeout_summary.strip():
                try:
                    from app.services.claim_validator import (
                        enforce_scientific_conclusion_gate,
                    )

                    timeout_summary, timeout_conclusion_violations = (
                        enforce_scientific_conclusion_gate(
                            timeout_summary, timeout_tool_results
                        )
                    )
                    if timeout_conclusion_violations:
                        timeout_validation_summary.update({
                            "citation_gate": "blocked",
                            "blocked": True,
                            "reason": "scientific_conclusion_scope",
                            "interventions": [{
                                "gate": "scientific_conclusion_scope",
                                "action": "blocked",
                                "reason": "unmatched_conclusion_attestation",
                            }],
                        })
                except Exception as exc:
                    timeout_summary = (
                        "The workflow timed out and scientific-conclusion "
                        "validation could not complete. No scientific conclusion "
                        "is cleared for display; review the tool cards and rerun."
                    )
                    timeout_validation_summary.update({
                        "citation_gate": "blocked",
                        "blocked": True,
                        "reason": "scientific_conclusion_validation_error",
                        "interventions": [{
                            "gate": "scientific_conclusion_scope",
                            "action": "blocked",
                            "reason": "validation_error",
                            "error_class": exc.__class__.__name__,
                        }],
                    })
            if timeout_summary.strip():
                from app.services.server_evidence import append_server_evidence

                await append_server_evidence(
                    session_id=chat_session_id,
                    owner_id=owner_id,
                    run_id=_agent_run_id,
                    assistant_reply=timeout_summary,
                    tool_results=timeout_tool_results,
                    validation_summary=timeout_validation_summary,
                )
                logger.warning(
                    "AI workflow timed out after %ss; emitting tool-grounded "
                    "timeout fallback from %d streamed tool result(s).",
                    timeout_s,
                    len(timeout_tool_results),
                )
                yield f"data: {json.dumps({'type': 'status', 'message': f'AI workflow timed out after {timeout_s}s; returning a tool-grounded partial summary.'})}\n\n"
                yield f"data: {json.dumps({'type': 'text', 'content': timeout_summary})}\n\n"
                for action in _tool_results_to_actions(timeout_tool_results):
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': action.get('action'), 'result': _slim_tool_result_for_sse(action.get('tool_result')), 'tool_call_id': action.get('_tool_call_id')}, default=str)}\n\n"
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
                owner_id=owner_id,
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


async def _register_active_python_session(
    user_id: str, python_session_id: str, db: AsyncSession
) -> None:
    """Index a session before priming, then close the deletion race."""
    from sqlalchemy import select
    from app.services.code_executor import (
        delete_user_session_registration_strict,
        mark_session_deleted,
        register_user_session,
    )

    await asyncio.to_thread(register_user_session, user_id, python_session_id)
    account_status = (
        await db.execute(
            select(User.account_status).where(User.id == uuid.UUID(user_id))
        )
    ).scalar_one_or_none()
    if str(account_status or "").upper() == "ACTIVE":
        return
    await asyncio.to_thread(mark_session_deleted, python_session_id)
    await asyncio.to_thread(
        delete_user_session_registration_strict, user_id, python_session_id
    )
    raise HTTPException(
        status_code=403,
        detail="Account deletion requested; chat execution was cancelled.",
    )


def _prime_adql_context_cache(context: dict | None, python_session_id: str) -> None:
    if not isinstance(context, dict):
        return
    from app.services.ai_tools import build_adql_result_set, replace_adql_result_sets, store_adql_result_set

    # These rows come straight from the client request body (session restore),
    # NOT from an archive query that actually ran this turn.  Stamp them so the
    # cached payload is honestly labelled at the trust boundary and a dishonest
    # client cannot have injected rows wear live-archive provenance.  (Consumers
    # of latest_adql that enforce the data-source contract must honour this
    # marker to fully downgrade the provenance — see cross-file note.)
    def _mark_client_restored(item: dict) -> dict:
        marked = dict(item)
        marked["data_origin"] = "client_restored"
        marked["restored_from_client"] = True
        return marked

    last_adql_result_sets = context.get("last_adql_result_sets")
    if isinstance(last_adql_result_sets, list) and last_adql_result_sets:
        replace_adql_result_sets(
            python_session_id,
            [_mark_client_restored(item) for item in last_adql_result_sets if isinstance(item, dict)],
        )
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
    store_adql_result_set(python_session_id, _mark_client_restored(result_set))


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
            "validation_summary": single.get("validation_summary"),
        }

    agent_results: list[dict] = []
    handoff = None

    async def _forward_specialist_event(
        event: dict,
        specialist: str,
    ) -> None:
        if on_event is None:
            return
        if event.get("type") == "honest_abstention":
            # A specialist abstention is intermediate evidence, not the final
            # merged assistant state.  Forwarding it verbatim makes the
            # frontend permanently prefer an abstention card over the later
            # merged reply, so surface it only as merge-pending status.
            await on_event({
                "type": "status",
                "message": (
                    f"Research specialist {specialist} issued an honest "
                    "abstention; the final research merge is still pending."
                ),
                "specialist_abstention": event.get("payload"),
            })
            return
        await on_event(event)

    for index, agent_name in enumerate(agent_names):
        agent_runtime = orchestrator.get_agent_runtime(
            agent_name,
            str(runtime.get("user_context", "") or ""),
        )
        agent_messages = deepcopy(messages)
        if handoff is not None:
            agent_messages.append({"role": "user", "content": _build_agent_handoff_message(handoff)})

        agent_abstention_event: dict = {}

        async def specialist_event(
            event: dict,
            _agent_name: str = agent_name,
            _abstention_event: dict = agent_abstention_event,
        ) -> None:
            if event.get("type") == "honest_abstention":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    _abstention_event["payload"] = dict(payload)
            await _forward_specialist_event(event, _agent_name)

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
            "on_event": specialist_event if on_event is not None else None,
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
                "validation_summary": result.get("validation_summary"),
                "honest_abstention": bool(
                    result.get("honest_abstention")
                    or agent_abstention_event.get("payload")
                ),
                "abstention_reason": result.get("abstention_reason"),
                "abstention_payload": agent_abstention_event.get("payload"),
            }
        )
        if index < len(agent_names) - 1:
            handoff = await orchestrator.summarize_handoff(
                agent_name,
                agent_names[index + 1],
                result["reply"],
            )

    latest_research_user_text = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            latest_research_user_text = str(message.get("content") or "")
            break
    merged_research_workflow = _is_research_program_workflow(latest_research_user_text)

    merged_actions: list[dict] = []
    merged_tool_results: list[dict] = []
    for result in agent_results:
        merged_actions.extend(result["actions"])
        merged_tool_results.extend(result.get("tool_results", []))

    # Schema-v2 validation metadata is backend-owned state, not model prose.
    # Preserve it across a multi-specialist merge so capability-gap and
    # evidence receipts remain visible on the final assistant message.  Only
    # receipts with a valid deterministic digest are carried forward.
    _member_summaries = [
        result.get("validation_summary")
        for result in agent_results
        if isinstance(result.get("validation_summary"), dict)
    ]
    _task_kind_rank = {
        "general": 0,
        "research_exploration": 1,
        "deterministic_source_check": 2,
        "full_research": 3,
    }
    _member_task_kinds = [
        str(summary.get("task_kind") or "general")
        for summary in _member_summaries
    ]
    _merged_task_kind = max(
        _member_task_kinds or ["general"],
        key=lambda value: _task_kind_rank.get(value, -1),
    )

    def _unique_member_strings(field: str) -> list[str]:
        values: list[str] = []
        for summary in _member_summaries:
            raw_values = summary.get(field) or []
            if isinstance(raw_values, str):
                raw_values = [raw_values]
            for raw in raw_values if isinstance(raw_values, list) else []:
                value = str(raw or "").strip()
                if value and value not in values:
                    values.append(value)
        return values

    _member_missing_dependencies = _unique_member_strings(
        "missing_dependencies"
    )
    _member_safe_fallbacks = _unique_member_strings("safe_fallback")
    _member_limiting_stages = _unique_member_strings(
        "earliest_limiting_stage"
    )
    _member_summary_limited = any(
        bool(summary.get("limited"))
        or str(summary.get("response_disposition") or "full") != "full"
        for summary in _member_summaries
    )
    from app.services.agent_runtime.evidence_receipts import (
        validate_evidence_receipt,
    )

    _member_evidence_receipts: list[dict[str, Any]] = []
    _seen_receipt_digests: set[str] = set()
    for summary in _member_summaries:
        for receipt in summary.get("evidence_receipts") or []:
            if not validate_evidence_receipt(receipt):
                continue
            digest = str(receipt.get("receipt_sha256") or "")
            if digest in _seen_receipt_digests:
                continue
            _seen_receipt_digests.add(digest)
            _member_evidence_receipts.append(deepcopy(receipt))

    if agent_results and all(
        bool(result.get("honest_abstention")) for result in agent_results
    ):
        abstention_payloads = [
            result.get("abstention_payload")
            for result in agent_results
            if isinstance(result.get("abstention_payload"), dict)
        ]

        def _joined_abstention_field(field: str) -> str:
            values: list[str] = []
            for payload in abstention_payloads:
                value = str(payload.get(field) or "").strip()
                if value and value not in values:
                    values.append(value)
            return ", ".join(values)

        reasons = {
            str(
                (result.get("abstention_payload") or {}).get("reason")
                or result.get("abstention_reason")
                or "no_tools"
            )
            for result in agent_results
        }
        final_abstention_reason = (
            next(iter(reasons)) if len(reasons) == 1 else "mixed"
        )
        final_abstention_payload = {
            "failed_tools": _joined_abstention_field("failed_tools"),
            "empty_tools": _joined_abstention_field("empty_tools"),
            "rationale": (
                "All specialist agents honestly abstained; none produced "
                "claimable tool-backed evidence for a merged conclusion."
            ),
            "suggested_next_step": _joined_abstention_field(
                "suggested_next_step"
            ),
            "reason": final_abstention_reason,
            "agent": "merged_orchestrator",
        }
        if on_event is not None:
            try:
                await on_event({
                    "type": "honest_abstention",
                    "payload": final_abstention_payload,
                })
            except Exception as exc:
                logger.debug(
                    "Final merged abstention event emission failed: %s", exc
                )
        abstention_validation_summary: dict[str, Any] = {
            "schema_version": 2,
            "approval_state": APPROVAL_STATE_NONE,
            "numeric_gate": "not_run",
            "citation_gate": "not_run",
            "regen_count": 0,
            "blocked": False,
            "limited": bool(_member_missing_dependencies),
            "response_disposition": "abstention",
            "task_kind": _merged_task_kind,
            "earliest_limiting_stage": "all_specialists_honest_abstention",
            "missing_dependencies": _member_missing_dependencies,
            "safe_fallback": (
                _member_safe_fallbacks[0]
                if _member_safe_fallbacks
                else "Provide the required registered evidence and rerun."
            ),
            "reason": "all_specialists_honest_abstention",
            "interventions": [],
        }
        if _member_evidence_receipts:
            abstention_validation_summary["evidence_receipts"] = (
                _member_evidence_receipts
            )
        return {
            "reply": _render_abstention_card(
                final_abstention_payload,
                final_abstention_reason,
            ),
            "actions": merged_actions,
            "tool_results": merged_tool_results,
            "hit_deadline": any(
                bool(result.get("hit_deadline")) for result in agent_results
            ),
            "hit_iteration_cap": any(
                bool(result.get("hit_iteration_cap"))
                for result in agent_results
            ),
            "honest_abstention": True,
            "abstention_reason": final_abstention_reason,
            "validation_summary": abstention_validation_summary,
        }
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
    # 2026-07-03 honesty surfacing: merged-reply validation summary.  The
    # merged prose is a NEW assistant reply (R21), so its gate states start
    # at "not_run" and are only upgraded by checks that actually ran below.
    _merged_numeric_state = "not_run"
    _merged_citation_state = "not_run"
    _merged_blocked = False
    _merged_limited = False
    _merged_dataset_identity_enforced = False
    _merged_fact_check_failed = False
    _merged_fact_check_no_safe_summary = False
    _merged_report_export_failed = False
    _merged_gate_interventions: list[dict] = []
    _merged_fact_failure_notice = (
        "Automatic fact verification of the merged research reply failed, "
        "so no merged claim or report was cleared for use. Review the failed "
        "Fact Check result and rerun verification before relying on it."
    )
    _merged_report_failure_notice = (
        "Automatic merged research-report export failed. The tool-grounded "
        "analysis remains visible, but the requested report artifact was not "
        "created or cleared for use. Rerun export before treating the merged "
        "workflow as complete."
    )
    _merged_summary_reason: str | None = (
        None if merged_reply.strip() else "empty_merged_reply"
    )

    async def _emit_merged_event(event: dict) -> None:
        if on_event is not None:
            try:
                await on_event(event)
            except Exception as exc:
                logger.debug(
                    "Merged automatic event emission failed for %s: %s",
                    event.get("type"),
                    exc,
                )

    async def _merged_gate_event(
        gate: str,
        action: str,
        *,
        reason: str = "",
        details: dict | None = None,
        draft: str = "",
        final: str = "",
    ) -> None:
        # The merged reply's counterpart of the loop's ``_gate_event``: the
        # same three sinks (local JSONL, gate_event_total counter, redacted
        # SSE copy) built from the same observability helpers.  The loop's
        # emitter is a closure over its own turn state, so the composition
        # is repeated here rather than imported.  Must never affect the
        # reply; everything is wrapped.  (Codex review 2026-09-03,
        # PRRT_kwDORoeoE86evFtk: the merged approval marker recorded its
        # intervention in the summary but emitted no gate event.)
        try:
            from app.observability.gate_events import (
                append_gate_event_jsonl,
                build_gate_event,
                redact_event_for_wire,
            )
            from app.observability.metrics import record_counter
            from app.services.agent_runtime.honesty import redact_gated_values

            evt = build_gate_event(
                gate=gate,
                action=action,
                reason=reason,
                agent="merged_orchestrator",
                details=dict(details or {}),
                tools_run=[
                    str(r.get("tool")) for r in merged_tool_results
                    if isinstance(r, dict) and r.get("tool")
                ],
                draft=draft,
                final=final,
                chat_session_id=str(chat_session_id) if chat_session_id else None,
                python_session_id=str(python_session_id) if python_session_id else None,
            )
            append_gate_event_jsonl(evt)
            record_counter("gate_event_total", 1.0, gate=gate, action=action)

            def _redact_for_wire(text: str) -> tuple[str, int]:
                return redact_gated_values(text, messages, merged_tool_results)

            await _emit_merged_event(redact_event_for_wire(evt, _redact_for_wire))
        except Exception as exc:
            logger.debug("merged gate_event emission failed: %s", exc)

    if merged_reply.strip():
        try:
            from app.services.claim_validator import (
                blocked_reply_with_narrative,
                attach_draft_to_banner,
                limited_citation_reply_text,
                blocked_unsupported_narrative_reply_text,
                citation_violations_should_block,
                is_empty_turn,
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
            # The merge-time gates ran — default both states to their
            # passing values, then downgrade in the blocking branches.
            _merged_citation_state = "passed"
            _merged_numeric_state = (
                "skipped_no_data" if is_empty_turn(merged_tool_results) else "passed"
            )
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
                _merged_citation_state = "blocked"
                _merged_blocked = True
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
                        "## ⚠ Limited answer: citation provenance gaps\n\n"
                        "The supported parts of the answer remain visible, but "
                        "the platform's provenance gate flagged citations that "
                        "the merged tool results did not support. Treat only the "
                        "flagged items as **NOT verified** and re-run the relevant "
                        "tools before quoting them in a paper.\n\n"
                        + limited_citation_reply_text(citation_violations)
                    )
                else:
                    merged_reply = limited_citation_reply_text(citation_violations)
                _merged_citation_state = "limited"
                _merged_limited = True
                _merged_gate_interventions.append({
                    "gate": "citation_methodology",
                    "action": "annotated_limited",
                    "reason": "merged_reply_citation_gap",
                })
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
                _merged_numeric_state = "blocked"
                _merged_blocked = True
        except Exception as exc:
            # Merge-time validation did not complete — never report a pass.
            _merged_numeric_state = "not_run"
            _merged_citation_state = "not_run"
            _merged_summary_reason = "merge_validation_error"
            if merged_research_workflow:
                logger.exception(
                    "Merged-reply claim validation failed closed for research workflow"
                )
                safe_summary = _tool_grounded_summary(
                    merged_tool_results, latest_research_user_text
                )
                merged_reply = (
                    "The specialist agents completed tool work, but the platform "
                    "could not safely validate the merged prose. Below is a "
                    "tool-grounded fallback summary; no unsupported conclusion is "
                    "added.\n\n"
                    + (
                        safe_summary
                        or "The tool cards below are the source of truth for this turn. "
                        "Please rerun the missing evidence path before quoting any "
                        "posterior, fit, or tension values."
                    )
                )
            else:
                logger.warning("Merged-reply claim validation failed open: %s", exc)

    _merged_fact_call_id: str | None = None
    if merged_research_workflow and merged_reply.strip():
        try:
            from app.services.research_program import verify_research_facts

            fact_input = {
                "tool_results": _compact_tool_results_for_evidence(merged_tool_results),
                "final_reply": merged_reply,
            }
            _merged_fact_call_id = f"auto_fact_check_{uuid.uuid4().hex}"
            await _emit_merged_event({
                "type": "tool_call",
                "agent": "merged_orchestrator",
                "tool": "verify_research_facts",
                "input": {
                    "tool_result_count": len(merged_tool_results),
                    "final_reply_chars": len(merged_reply),
                },
                "automatic": True,
            })
            fact_result = verify_research_facts(**fact_input)
            if not isinstance(fact_result, dict):
                raise TypeError("verify_research_facts returned a non-object result")
            fact_tool_result = {
                "id": _merged_fact_call_id,
                "tool": "verify_research_facts",
                "input": fact_input,
                "result": fact_result,
            }
            merged_tool_results.append(fact_tool_result)
            merged_actions.extend(_tool_results_to_actions([fact_tool_result]))
            await _emit_merged_event({
                "type": "tool_result",
                "agent": "merged_orchestrator",
                "tool": "verify_research_facts",
                "result": fact_result,
                "live": True,
                "tool_call_id": _merged_fact_call_id,
                "automatic": True,
            })
            if fact_result.get("status") == "blocked":
                # HOLD (mirror the single-agent path): keep the tool-grounded core
                # + surface held claims as a footer instead of nuking the reply.
                safe_summary = (
                    _research_tool_grounded_summary(merged_tool_results)
                    or _cosmology_tool_grounded_summary(
                        merged_tool_results, latest_research_user_text or ""
                    )
                )
                held_claims = [
                    c for c in (fact_result.get("claims") or [])
                    if c.get("status") in {"unsupported", "contradicted"}
                ]
                if safe_summary:
                    merged_reply = safe_summary
                    if held_claims:
                        merged_reply += (
                            f"\n\n⚠ {len(held_claims)} claim(s) in the draft were held — not "
                            "grounded by any tool this turn — and excluded from the result "
                            "above. See the Fact Check panel for each held claim and how to "
                            "ground it."
                        )
                    # Shipped prose was downgraded to a tool-grounded summary
                    # (mirrors the single-agent fact_verification mapping).
                    if _merged_numeric_state != "blocked":
                        _merged_numeric_state = "regenerated"
                    if _merged_citation_state != "blocked":
                        _merged_citation_state = "regenerated"
                    _merged_gate_interventions.append({
                        "gate": "fact_verification",
                        "action": "downgraded_summary",
                        "reason": "fact_check_held",
                    })
                else:
                    merged_reply = (
                        "The research run completed, but fact verification found "
                        "a contradicted claim in the merged draft. Please review "
                        "the Fact Check card and rerun the missing evidence path "
                        "before using the result."
                    )
                    _merged_numeric_state = "blocked"
                    _merged_citation_state = "blocked"
                    _merged_blocked = True
                    _merged_fact_check_no_safe_summary = True
                    _merged_gate_interventions.append({
                        "gate": "fact_verification",
                        "action": "blocked",
                        "reason": "fact_check_no_summary",
                    })
        except Exception as exc:
            _merged_fact_check_failed = True
            _merged_blocked = True
            _merged_numeric_state = "blocked"
            _merged_citation_state = "blocked"
            _merged_summary_reason = "automatic_fact_check_failed"
            if _merged_fact_call_id is None:
                _merged_fact_call_id = f"auto_fact_check_{uuid.uuid4().hex}"
            fact_failure_result = {
                "success": False,
                "__tool_status__": "FAILED",
                "analysis_status": "FACT_CHECK_FAILED",
                "status": "blocked",
                "publication_ready": False,
                "__do_not_claim__": True,
                "error_class": "automatic_fact_check_failed",
                "error": "Automatic merged fact verification failed.",
                "fact_check_report": {
                    "status": "blocked",
                    "verified_claim_count": 0,
                    "unsupported_claim_count": 0,
                    "claims": [],
                },
            }
            failed_fact_tool_result = {
                "id": _merged_fact_call_id,
                "tool": "verify_research_facts",
                "input": {
                    "tool_result_count": len(merged_tool_results),
                    "final_reply_chars": len(merged_reply),
                },
                "result": fact_failure_result,
            }
            existing_fact_result = next(
                (
                    tr
                    for tr in merged_tool_results
                    if tr.get("id") == _merged_fact_call_id
                ),
                None,
            )
            if existing_fact_result is None:
                merged_tool_results.append(failed_fact_tool_result)
                merged_actions.extend(
                    _tool_results_to_actions([failed_fact_tool_result])
                )
            else:
                existing_fact_result.update(failed_fact_tool_result)
            await _emit_merged_event({
                "type": "tool_result",
                "agent": "merged_orchestrator",
                "tool": "verify_research_facts",
                "result": fact_failure_result,
                "live": True,
                "tool_call_id": _merged_fact_call_id,
                "automatic": True,
            })
            merged_reply = _merged_fact_failure_notice
            _merged_gate_interventions.append({
                "gate": "fact_verification",
                "action": "blocked",
                "reason": "automatic_fact_check_failed",
            })
            logger.warning(
                "Merged research fact verification failed closed: %s", exc
            )

    _merged_report_call_id: str | None = None
    if (
        merged_research_workflow
        and not _merged_fact_check_failed
        and not _merged_fact_check_no_safe_summary
    ):
        try:
            from app.services.research_program import export_research_report

            report_input = {
                "research_plan": _research_plan_from_tool_results(merged_tool_results),
                "evidence_graph": _research_evidence_graph_from_tool_results(merged_tool_results),
                "tool_results": merged_tool_results,
                "title": latest_research_user_text[:180] if latest_research_user_text else None,
            }
            _merged_report_call_id = (
                f"auto_research_report_{uuid.uuid4().hex}"
            )
            await _emit_merged_event({
                "type": "tool_call",
                "agent": "merged_orchestrator",
                "tool": "export_research_report",
                "input": {
                    "tool_result_count": len(merged_tool_results),
                    "title": report_input["title"],
                    "report_scope": "merged",
                },
                "automatic": True,
            })
            report_result = export_research_report(**report_input)
            report_tool_result = {
                "id": _merged_report_call_id,
                "tool": "export_research_report",
                "input": {
                    "research_plan": report_input["research_plan"],
                    "evidence_graph": report_input["evidence_graph"],
                    "title": report_input["title"],
                    "report_scope": "merged",
                },
                "result": report_result,
            }
            merged_tool_results.append(report_tool_result)
            merged_actions.extend(_tool_results_to_actions([report_tool_result]))
            await _emit_merged_event({
                "type": "tool_result",
                "agent": "merged_orchestrator",
                "tool": "export_research_report",
                "result": report_result,
                "live": True,
                "tool_call_id": _merged_report_call_id,
                "automatic": True,
            })
        except Exception as exc:
            _merged_report_export_failed = True
            if _merged_report_call_id is None:
                _merged_report_call_id = (
                    f"auto_research_report_{uuid.uuid4().hex}"
                )
            report_failure_result = {
                "success": False,
                "__tool_status__": "FAILED",
                "analysis_status": "REPORT_EXPORT_FAILED",
                "publication_ready": False,
                "__do_not_claim__": True,
                "error_class": "automatic_report_export_failed",
                "error": "Automatic merged research-report export failed.",
            }
            failed_report_tool_result = {
                "id": _merged_report_call_id,
                "tool": "export_research_report",
                "input": {
                    "tool_result_count": len(merged_tool_results),
                    "title": (
                        latest_research_user_text[:180]
                        if latest_research_user_text
                        else None
                    ),
                    "report_scope": "merged",
                },
                "result": report_failure_result,
            }
            existing_report_result = next(
                (
                    tr
                    for tr in merged_tool_results
                    if tr.get("id") == _merged_report_call_id
                ),
                None,
            )
            if existing_report_result is None:
                merged_tool_results.append(failed_report_tool_result)
                merged_actions.extend(
                    _tool_results_to_actions([failed_report_tool_result])
                )
            else:
                existing_report_result.update(failed_report_tool_result)
            await _emit_merged_event({
                "type": "tool_result",
                "agent": "merged_orchestrator",
                "tool": "export_research_report",
                "result": report_failure_result,
                "live": True,
                "tool_call_id": _merged_report_call_id,
                "automatic": True,
            })
            _report_failure_draft = merged_reply
            merged_reply = (
                merged_reply.rstrip()
                + "\n\n---\n\n"
                + _merged_report_failure_notice
            )
            _merged_numeric_state = "blocked"
            _merged_citation_state = "blocked"
            _merged_blocked = True
            _merged_summary_reason = "automatic_report_export_failed"
            _merged_gate_interventions.append({
                "gate": "report_export",
                "action": "blocked",
                "reason": "automatic_report_export_failed",
                "draft_changed": _report_failure_draft != merged_reply,
            })
            logger.warning("Merged research report export failed: %s", exc)

    _merged_identity_draft = merged_reply
    merged_reply, _merged_dataset_identity_enforced = (
        _enforce_cosmology_dataset_identity(
            merged_reply,
            merged_tool_results,
            latest_research_user_text,
        )
    )
    if _merged_dataset_identity_enforced:
        if _merged_fact_check_failed:
            merged_reply += "\n\n---\n\n" + _merged_fact_failure_notice
        if _merged_report_export_failed:
            merged_reply += "\n\n---\n\n" + _merged_report_failure_notice
        _merged_identity_action = "downgraded_summary"
        _merged_identity_reason = "requested_release_mismatch"
        try:
            from app.services.claim_validator import (
                blocked_reply_with_narrative,
                validate_claims,
            )

            identity_validation = validate_claims(
                merged_reply, merged_tool_results
            )
            if identity_validation.ok:
                if _merged_numeric_state != "blocked":
                    _merged_numeric_state = "regenerated"
            else:
                merged_reply = blocked_reply_with_narrative(
                    identity_validation, merged_reply
                )
                _merged_numeric_state = "blocked"
                _merged_blocked = True
                _merged_identity_action = "blocked"
                _merged_identity_reason = (
                    "dataset_identity_claim_validation_failed"
                )
        except Exception as exc:
            logger.exception(
                "Merged dataset-identity summary validation failed: %s", exc
            )
            _merged_numeric_state = "blocked"
            _merged_blocked = True
            _merged_identity_action = "blocked"
            _merged_identity_reason = "dataset_identity_validation_error"
            merged_reply += (
                "\n\n---\n\nDataset identity was corrected from the merged "
                "tool outputs, but secondary claim validation failed. Treat "
                "this reply as blocked until validation is rerun."
            )
            if not (_merged_fact_check_failed or _merged_report_export_failed):
                _merged_summary_reason = "dataset_identity_validation_error"

    # The merged prose is a new public reply and later deterministic fallbacks
    # may replace text that was checked earlier.  Apply the same reusable
    # qualitative-science gate once more at the final merged boundary.
    try:
        from app.services.claim_validator import (
            enforce_scientific_conclusion_gate,
        )

        _merged_conclusion_draft = merged_reply
        merged_reply, merged_conclusion_violations = (
            enforce_scientific_conclusion_gate(
                merged_reply, merged_tool_results
            )
        )
        if merged_conclusion_violations:
            _merged_citation_state = "blocked"
            _merged_blocked = True
            _merged_summary_reason = "scientific_conclusion_scope"
            _merged_gate_interventions.append({
                "gate": "scientific_conclusion_scope",
                "action": "blocked",
                "reason": "unmatched_conclusion_attestation",
                "draft_changed": _merged_conclusion_draft != merged_reply,
            })
    except Exception as exc:
        merged_reply = (
            "Scientific-conclusion validation failed, so no qualitative "
            "scientific conclusion is cleared for display. Review the current "
            "tool evidence and rerun the validation step."
        )
        _merged_citation_state = "blocked"
        _merged_blocked = True
        _merged_summary_reason = "scientific_conclusion_validation_error"
        _merged_gate_interventions.append({
            "gate": "scientific_conclusion_scope",
            "action": "blocked",
            "reason": "validation_error",
            "error_class": exc.__class__.__name__,
        })
        logger.exception("Merged scientific-conclusion gate failed closed")

    # Approval language that no stored review backs, applied to the MERGED
    # prose.  The per-specialist marker in the agent loop does not cover this
    # text: merge_responses writes a new public reply, and the merged boundary
    # only re-runs the numeric, citation and scientific-conclusion gates — all
    # of which pass a line like "APPROVED by reviewer: H0 = 67.36" because the
    # number really did come from a claimable tool result.  Same gate event and
    # same limited flag as the loop, so the badge cannot differ between a
    # single-specialist and a multi-specialist turn.
    _merged_approval_draft = merged_reply
    merged_reply, _merged_approval_marked = mark_unapproved_claims(
        merged_reply, merged_tool_results
    )
    if _merged_approval_marked:
        _merged_limited = True
        _merged_gate_interventions.append({
            "gate": "approval_marker",
            "action": "annotated_limited",
            "reason": "no_bound_claim_audit_review",
            "marked_lines": _merged_approval_marked,
            "draft_changed": _merged_approval_draft != merged_reply,
        })
        await _merged_gate_event(
            "approval_marker",
            "annotated_limited",
            reason="no_bound_claim_audit_review",
            details={"marked_lines": _merged_approval_marked},
            draft=_merged_approval_draft,
            final=merged_reply,
        )

    # Assemble the merged validation summary.  Top-level states describe the
    # SHIPPED merged prose (validated above against the union of tool
    # results); per-agent interventions are folded in so a member reply
    # that was regenerated/blocked upstream stays visible.  A "passed"
    # state is upgraded to "regenerated" when any member intervention
    # touched that gate family — understate rather than overstate.
    from app.services.agent_runtime.loop import (
        _BLOCKING_GATE_ACTIONS,
        _CITATION_GATE_FAMILY,
        _LIMITING_GATE_ACTIONS,
        _NUMERIC_GATE_FAMILY,
        _VALIDATION_SUMMARY_MAX_INTERVENTIONS,
    )

    _member_interventions: list[dict] = list(_merged_gate_interventions)
    for s in _member_summaries:
        for item in s.get("interventions") or []:
            if isinstance(item, dict):
                _member_interventions.append(item)
    if _merged_dataset_identity_enforced:
        _member_interventions.append({
            "gate": "dataset_identity",
            "action": _merged_identity_action,
            "reason": _merged_identity_reason,
            "draft_changed": _merged_identity_draft != merged_reply,
        })
    unrecovered_member_report_failure = (
        not _successful_research_report_export(merged_tool_results)
        and any(
            item.get("gate") == "report_export"
            and item.get("action") in _BLOCKING_GATE_ACTIONS
            for item in _member_interventions
        )
    )
    if unrecovered_member_report_failure:
        _merged_numeric_state = "blocked"
        _merged_citation_state = "blocked"
        _merged_blocked = True
        if not _merged_summary_reason:
            _merged_summary_reason = "member_report_export_failed"
    if _merged_numeric_state == "passed" and any(
        i.get("gate") in _NUMERIC_GATE_FAMILY for i in _member_interventions
    ):
        _merged_numeric_state = "regenerated"
    if _merged_citation_state == "passed":
        citation_interventions = [
            i for i in _member_interventions
            if i.get("gate") in _CITATION_GATE_FAMILY
        ]
        if any(i.get("action") in _LIMITING_GATE_ACTIONS for i in citation_interventions):
            _merged_citation_state = "limited"
            _merged_limited = True
        elif citation_interventions:
            _merged_citation_state = "regenerated"
    merged_limited = (
        _merged_limited
        or _member_summary_limited
        or bool(_member_missing_dependencies)
    )
    merged_limiting_intervention = next(
        (
            item
            for item in _member_interventions
            if item.get("action")
            in _BLOCKING_GATE_ACTIONS | _LIMITING_GATE_ACTIONS
        ),
        None,
    )
    # Codex review P2 (PR #46, round 67): a merged reply whose every
    # specialist refused or abstained is not a "limited" answer — there is
    # no partial answer to limit, and the carried evidence receipts still
    # say refusal. Propagate the strongest declined disposition so the
    # badge matches the receipts; mixed merges (some member answered)
    # stay "limited" — understate rather than overstate.
    _member_dispositions = [
        str(s.get("response_disposition") or "full")
        for s in _member_summaries
    ]
    _all_members_declined = bool(_member_dispositions) and all(
        d in ("refusal", "abstention") for d in _member_dispositions
    )
    merged_validation_summary: dict = {
        "schema_version": 2,
        # Merging does not create an approval either: no chat path can read or
        # write a ClaimAuditReview row, so the merged badge states "none"
        # rather than leaving the reader to infer it from a missing field.
        "approval_state": APPROVAL_STATE_NONE,
        "numeric_gate": _merged_numeric_state,
        "citation_gate": _merged_citation_state,
        "regen_count": sum(
            int(s.get("regen_count", 0) or 0) for s in _member_summaries
        ),
        "blocked": _merged_blocked,
        "limited": merged_limited,
        "response_disposition": (
            "hard_block"
            if _merged_blocked
            else "refusal"
            if _all_members_declined and "refusal" in _member_dispositions
            else "abstention"
            if _all_members_declined
            else "limited"
            if merged_limited
            else "full"
        ),
        "task_kind": _merged_task_kind,
        "earliest_limiting_stage": (
            _merged_summary_reason
            or str((merged_limiting_intervention or {}).get("gate") or "")
            or (_member_limiting_stages[0] if _member_limiting_stages else None)
        ),
        "missing_dependencies": _member_missing_dependencies,
        "safe_fallback": (
            "Review the merged gate interventions and rerun the failed "
            "validation path before relying on the result."
            if _merged_blocked
            else (
                _member_safe_fallbacks[0]
                if _member_safe_fallbacks
                else None
            )
        ),
        "interventions": _member_interventions[:_VALIDATION_SUMMARY_MAX_INTERVENTIONS],
    }
    if _member_evidence_receipts:
        merged_validation_summary["evidence_receipts"] = (
            _member_evidence_receipts
        )
    if _merged_summary_reason:
        merged_validation_summary["reason"] = _merged_summary_reason

    return {
        "reply": merged_reply,
        "actions": merged_actions,
        "tool_results": merged_tool_results,
        "hit_deadline": any(bool(r.get("hit_deadline")) for r in agent_results),
        "hit_iteration_cap": any(bool(r.get("hit_iteration_cap")) for r in agent_results),
        "validation_summary": merged_validation_summary,
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
    if _local_backend_configured():
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
    _enforce_starter_daily_quota(
        user,
        req.context,
        provider_api_keys,
        request=request,
    )
    preferred_backend = _preferred_backend(req.context)
    preferred_model_profile = _preferred_model_profile(req.context)
    workflow_budget = _workflow_budget_config(_infer_workflow_budget_mode(req))

    claude_messages: list[dict] = _normalize_messages(req.messages)
    chat_session_id = await _validated_current_session_id(req.context, user, db)
    runtime = await _build_runtime(req, user, db)
    from app.services.ai_tools import build_trusted_python_session_id

    python_session_id = build_trusted_python_session_id(
        user_id=str(user.id),
        chat_session_id=chat_session_id,
        requested_session_id=(req.context or {}).get(
            "python_session_id", "default"
        ),
    )
    await _register_active_python_session(str(user.id), python_session_id, db)
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
        from app.services.server_evidence import append_server_evidence

        await append_server_evidence(
            session_id=chat_session_id,
            owner_id=str(user.id),
            run_id=uuid.uuid4().hex,
            assistant_reply=str(response.get("reply") or ""),
            tool_results=(
                response.get("tool_results")
                if isinstance(response.get("tool_results"), list)
                else []
            ),
            validation_summary=(
                response.get("validation_summary")
                if isinstance(response.get("validation_summary"), dict)
                else None
            ),
        )
        return ChatResponse(
            reply=response["reply"],
            actions=response["actions"],
            hit_iteration_cap=bool(response.get("hit_iteration_cap", False)),
            validation_summary=(
                response.get("validation_summary")
                if isinstance(response.get("validation_summary"), dict)
                else None
            ),
        )

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
        # Call the ADQL executor function directly. (2026-06-11: the public
        # /adql/query route wrapper was removed as a dead unauthenticated
        # endpoint; this arm was ALREADY broken before that — the route
        # wrapper's rate-limiter signature mis-bound `adql_query(req)` —
        # so calling execute_adql_query is both the fix and the survivor.)
        from app.api.integration import ADQLRequest, execute_adql_query

        req = ADQLRequest(
            query=action.get("query", ""), service=action.get("service", "gaia")
        )
        result = await execute_adql_query(req)
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
    # Deprecated compatibility field. It is deliberately ignored: audit_log
    # is now server-owned publication evidence and clients may not overwrite
    # or append to it.
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
        audit_log=None,
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
