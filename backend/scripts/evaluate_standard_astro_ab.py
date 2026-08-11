#!/usr/bin/env python3
"""Run the fixed direct-model versus Standard Astro cosmology evaluation.

The script prints only user-visible answers and compact gate/tool metadata. It
does not serialize system prompts, credentials, messages sent to the backend,
or raw execution traces.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import time
import uuid

from app.ai.inference_router import LocalBackend
from app.ai.model_profiles import resolve_model_profile
from app.api import chat
from app.services.ai_tools import TOOLS


MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
)
CASES = {
    "A2_hubble_tension": (
        "Quote the Hubble tension between Planck 2018 CMB-only and Riess et "
        "al. 2022 SH0ES. Use compare_luminosity_distances when it is "
        "available to derive the percent offset; do not fabricate values, and "
        "distinguish a published-H0 anchor comparison from any per-source "
        "luminosity-distance calculation."
    ),
    "B1_desi_dr1_ap": (
        "Using the registered DESI DR1 BAO data, perform an "
        "Alcock-Paczynski geometric consistency test and report any best-fit "
        "Omega_m and 1-sigma interval only if it is actually executable. "
        "Explain which common distance scale cancels. Do not substitute a "
        "recalled literature constraint for a computed result or invent values."
    ),
    "C1_full_ede_gap": (
        "Run a joint full-likelihood analysis using Planck 2018 high-ell "
        "TTTEEE plus lensing, DESI DR2 BAO, and Pantheon+ under an early-dark-"
        "energy model. Report posterior H0 and Delta chi-squared versus LCDM. "
        "If the exact likelihoods, sampler, or model implementation are not "
        "executable, do not replace them with compressed approximations: state "
        "the capability gap and what, if anything, can still be concluded."
    ),
}

DIRECT_SYSTEM = (
    "You are completing a closed-book observational-cosmology evaluation with "
    "no external tools. Do not claim to have queried data or run a likelihood. "
    "Separate reliably attributed publication values from calculations and "
    "from unavailable execution. When evidence or capability is missing, give "
    "a limited answer that names the gap instead of inventing a result."
)


def _profile(model: str):
    profile_id = (
        "local:claude-cli" if model.startswith("claude-") else "local:openai-cli"
    )
    base = resolve_model_profile("local", profile_id)
    return replace(
        base,
        model_id=model,
        resolved_model_id=model,
        display_name=model,
    )


def _compact_tools(result: dict) -> list[dict]:
    compact = []
    for item in result.get("tool_results", []):
        if not isinstance(item, dict):
            continue
        payload = item.get("result")
        payload = payload if isinstance(payload, dict) else {}
        compact.append({
            "tool": item.get("tool"),
            "status": payload.get("__tool_status__"),
            "success": payload.get("success"),
            "analysis_status": payload.get("analysis_status"),
            "error_class": payload.get("error_class"),
        })
    return compact


async def _run_direct(model: str, case_id: str, prompt: str) -> dict:
    started = time.monotonic()
    try:
        response = await LocalBackend().complete(
            [{"role": "user", "content": prompt}],
            system=DIRECT_SYSTEM,
            tools=[],
            max_tokens=1800,
            temperature=0.0,
            request_timeout=180,
            model_profile=_profile(model),
        )
        return {
            "model": model,
            "condition": "direct",
            "case_id": case_id,
            "status": "completed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "reply": response.get("content"),
            "model_name": response.get("model_name"),
        }
    except Exception as exc:  # preserve the rest of the matrix
        return {
            "model": model,
            "condition": "direct",
            "case_id": case_id,
            "status": "failed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


async def _run_standard(model: str, case_id: str, prompt: str) -> dict:
    started = time.monotonic()
    try:
        result = await chat._run_agent_loop(
            system=chat.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            tools=chat._filter_tools_by_research_focus(list(TOOLS)),
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id=f"ab-{case_id}-{uuid.uuid4().hex}",
            preferred_backend="local",
            model_profile=_profile(model),
            workflow_budget={
                "mode": "default",
                "max_iterations": 5,
                "agent_loop_seconds": 240,
                "summary_reserve_seconds": 30,
            },
        )
        return {
            "model": model,
            "condition": "standard_astro",
            "case_id": case_id,
            "status": "completed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "reply": result.get("reply"),
            "tools": _compact_tools(result),
            "validation_summary": result.get("validation_summary"),
            "hit_deadline": result.get("hit_deadline", False),
            "hit_iteration_cap": result.get("hit_iteration_cap", False),
        }
    except Exception as exc:  # preserve the rest of the matrix
        return {
            "model": model,
            "condition": "standard_astro",
            "case_id": case_id,
            "status": "failed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=("direct", "standard_astro"),
        default=["direct", "standard_astro"],
    )
    parser.add_argument("--cases", nargs="+", choices=tuple(CASES), default=list(CASES))
    args = parser.parse_args()

    results = []
    for model in args.models:
        for condition in args.conditions:
            for case_id in args.cases:
                runner = _run_direct if condition == "direct" else _run_standard
                results.append(await runner(model, case_id, CASES[case_id]))
    print(json.dumps({
        "schema_version": 1,
        "models": args.models,
        "conditions": args.conditions,
        "cases": {case_id: CASES[case_id] for case_id in args.cases},
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
