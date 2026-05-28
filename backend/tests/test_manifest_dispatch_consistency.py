"""Manifest ↔ schema ↔ dispatch consistency gate.

Locks the contract that every tool declared in an active module manifest
(``backend/app/prompts/modules/<focus>/manifest.yaml``) has:

1. A schema entry in the global ``TOOLS`` list (so the LLM sees its
   parameter spec), and
2. Resolves through ``build_allowed_tools(focus)`` (so the focus gate
   actually exposes it at chat time).

Regression target: the 2026-05-27 ``45383ac`` fix where
``assess_bao_bin_anomaly`` was registered in schemas + dispatch but
omitted from ``modules/cosmology/manifest.yaml`` and silently stripped
by the focus gate — the LLM could never call it and the platform had
no surface-area gate against that class of drift.
"""
from __future__ import annotations

import pytest

ACTIVE_FOCI = ("cosmology", "solar_system", "exoplanet")


@pytest.fixture(autouse=True)
def _clear_prompt_loader_cache():
    from app.services.prompt_loader import build_allowed_tools, build_system_prompt
    build_system_prompt.cache_clear()
    build_allowed_tools.cache_clear()
    yield
    build_system_prompt.cache_clear()
    build_allowed_tools.cache_clear()


def _schema_names() -> set[str]:
    """Names of every tool that has a schema entry in global TOOLS."""
    from app.services.ai_tools import TOOLS
    return {entry["name"] for entry in TOOLS if isinstance(entry, dict) and "name" in entry}


def _manifest_tools(focus: str) -> list[str]:
    from app.services.prompt_loader import _module_dir, _read_yaml
    manifest = _read_yaml(_module_dir(focus) / "manifest.yaml")
    return list(manifest.get("tools") or [])


@pytest.mark.parametrize("focus", ACTIVE_FOCI)
def test_every_manifest_tool_has_a_schema(focus: str) -> None:
    """Each tool listed in manifest.yaml must have a corresponding schema in
    global TOOLS, otherwise the LLM is told the tool exists but it has no
    parameter spec to call it with."""
    manifest = _manifest_tools(focus)
    schemas = _schema_names()
    missing = sorted(set(manifest) - schemas)
    assert not missing, (
        f"{focus} manifest lists tools without a schema in TOOLS: {missing}. "
        "Either add the schema or remove from manifest.yaml."
    )


@pytest.mark.parametrize("focus", ACTIVE_FOCI)
def test_every_manifest_tool_passes_focus_gate(focus: str) -> None:
    """build_allowed_tools(focus) must surface every manifest tool —
    otherwise the focus filter would drop the tool at chat time even
    though it's declared in the manifest (the 45383ac regression
    class)."""
    from app.services.prompt_loader import build_allowed_tools
    manifest = set(_manifest_tools(focus))
    allowed = build_allowed_tools(focus)
    dropped = sorted(manifest - allowed)
    assert not dropped, (
        f"{focus} manifest tools dropped by focus gate: {dropped}. "
        "build_allowed_tools is supposed to be core ∪ manifest — investigate."
    )


def test_no_duplicate_tool_schemas_in_global_TOOLS() -> None:
    """Sibling-module schema injection (TOOLS.extend(...)) must not double-
    register a tool name. Duplicates would let one definition silently
    shadow another after a refactor."""
    from app.services.ai_tools import TOOLS
    names = [entry["name"] for entry in TOOLS if isinstance(entry, dict) and "name" in entry]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate tool schemas registered in TOOLS: {dupes}"


@pytest.mark.parametrize("focus", ACTIVE_FOCI)
def test_module_specific_tool_names_subset_of_schemas(focus: str) -> None:
    """The per-module dispatch frozenset (COSMOLOGY_TOOL_NAMES /
    SOLAR_SYSTEM_TOOL_NAMES / EXOPLANET_TOOL_NAMES) must be a subset of
    the global schema names — otherwise the dispatcher claims to handle
    a tool that has no schema, so execute_tool would be called with a
    tool the LLM cannot reasonably emit."""
    import importlib
    module_attr = {
        "cosmology": ("app.services.ai_tools_cosmology", "COSMOLOGY_TOOL_NAMES"),
        "solar_system": ("app.services.ai_tools_solar_system", "SOLAR_SYSTEM_TOOL_NAMES"),
        "exoplanet": ("app.services.ai_tools_exoplanet", "EXOPLANET_TOOL_NAMES"),
    }[focus]
    mod = importlib.import_module(module_attr[0])
    names = set(getattr(mod, module_attr[1]))
    schemas = _schema_names()
    orphan_dispatch = sorted(names - schemas)
    assert not orphan_dispatch, (
        f"{focus} dispatch set claims tools that have no schema: {orphan_dispatch}"
    )
