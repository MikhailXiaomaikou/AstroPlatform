"""M1 Phase 3 — manifest.yaml structural integrity tests.

Locks the contract that every module manifest references real tools,
has the expected status, and that no tool gets duplicated across
core + cosmology.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_PROMPTS_ROOT = (
    Path(__file__).resolve().parent.parent / "app" / "prompts"
)
_MODULES_DIR = _PROMPTS_ROOT / "modules"


def _tool_names_in_ai_tools() -> set[str]:
    from app.services.ai_tools import TOOLS

    return {t["name"] for t in TOOLS if isinstance(t, dict)}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _module_manifests() -> list[tuple[str, dict, Path]]:
    out: list[tuple[str, dict, Path]] = []
    for sub in sorted(_MODULES_DIR.iterdir()):
        if not sub.is_dir():
            continue
        manifest_path = sub / "manifest.yaml"
        if not manifest_path.exists():
            continue
        out.append((sub.name, _load_yaml(manifest_path), manifest_path))
    return out


# ── core/infrastructure.yaml ───────────────────────────────────────


def test_core_yaml_is_valid_and_nonempty():
    core_yaml = _load_yaml(_PROMPTS_ROOT / "core" / "infrastructure.yaml")
    assert isinstance(core_yaml, dict)
    tools = core_yaml.get("tools")
    assert isinstance(tools, list) and tools, "core/infrastructure.yaml has no tools"


def test_core_tools_all_exist_in_ai_tools():
    core_yaml = _load_yaml(_PROMPTS_ROOT / "core" / "infrastructure.yaml")
    core_tools = set(core_yaml.get("tools") or [])
    real_tools = _tool_names_in_ai_tools()
    missing = core_tools - real_tools
    assert not missing, f"core/infrastructure.yaml references nonexistent tools: {sorted(missing)}"


# ── per-module manifest.yaml ───────────────────────────────────────


@pytest.mark.parametrize("dirname,manifest,path", _module_manifests())
def test_manifest_has_required_keys(dirname: str, manifest: dict, path: Path):
    assert isinstance(manifest, dict), f"{path} is not a mapping"
    for key in ("name", "display", "status", "prompt_file"):
        assert key in manifest, f"{path} missing required key: {key!r}"
    assert manifest["status"] in {"active", "dormant"}, (
        f"{path} has unexpected status: {manifest['status']!r}"
    )


@pytest.mark.parametrize("dirname,manifest,path", _module_manifests())
def test_manifest_prompt_file_exists(dirname: str, manifest: dict, path: Path):
    prompt_path = path.parent / manifest["prompt_file"]
    assert prompt_path.exists(), f"{path} references missing prompt_file: {prompt_path}"


@pytest.mark.parametrize("dirname,manifest,path", _module_manifests())
def test_manifest_tools_all_exist_in_ai_tools(dirname: str, manifest: dict, path: Path):
    real_tools = _tool_names_in_ai_tools()
    manifest_tools = manifest.get("tools") or []
    if not manifest_tools:
        return  # empty manifest tools is allowed (e.g. high_z_galaxy, agn)
    missing = set(manifest_tools) - real_tools
    assert not missing, f"{path} references nonexistent tools: {sorted(missing)}"


def test_dormant_directories_have_dormant_status():
    """Any directory prefixed with `_dormant_` must declare status: dormant."""
    for dirname, manifest, path in _module_manifests():
        if dirname.startswith("_dormant_"):
            assert manifest["status"] == "dormant", (
                f"{path} is in _dormant_* but status={manifest['status']!r}"
            )


def test_cosmology_module_is_active():
    cosmology_manifest = _load_yaml(_MODULES_DIR / "cosmology" / "manifest.yaml")
    assert cosmology_manifest["status"] == "active"
    assert cosmology_manifest["name"] == "cosmology"


def test_no_tool_appears_in_both_core_and_a_module_manifest():
    """A tool should live in exactly one place — core OR a module manifest.
    Overlap tools intentionally appear in multiple module manifests (cosmology
    today, future modules later), but NOT in core.  This catches accidental
    duplication where a tool gets added to both core and a module."""
    core_yaml = _load_yaml(_PROMPTS_ROOT / "core" / "infrastructure.yaml")
    core_tools = set(core_yaml.get("tools") or [])

    for dirname, manifest, path in _module_manifests():
        manifest_tools = set(manifest.get("tools") or [])
        dup = core_tools & manifest_tools
        assert not dup, (
            f"{path} duplicates core tools: {sorted(dup)} "
            f"(should be in core OR module, not both)"
        )


# Tools whose implementation is intentionally retained in ai_tools.TOOLS but
# which belong to no active manifest in this cosmology-only repo. They fall into
# two buckets: shared multi-domain infrastructure kept for cosmology (pipeline
# generation/execution, generic analysis validation) and dormant-domain tool
# implementations whose prompt modules were extracted to standard-astro-verticals
# (paper-mining/tool-ontology, image reduction, stellar isochrone, galaxy Sérsic,
# pulsar timing, X-ray spectral fit, Keplerian RV). The cosmology focus gate hides
# every one of these from the LLM (build_allowed_tools("cosmology") excludes them);
# they remain only so the registry/dispatch stays internally consistent.
_RETAINED_UNMANIFESTED_TOOLS = frozenset({
    "generate_pipeline", "run_pipeline", "validate_analysis",
    "generate_paper_draft", "build_paper_mining_candidate_pool", "mine_paper_tools",
    "run_paper_tool_mining_batch", "run_paper_tool_mining_loop",
    "build_tool_ontology", "build_tool_gap_matrix", "rank_tool_implementation_queue",
    "reduce_ccd_image", "solve_astrometry", "extract_photometry", "extract_sources",
    "fit_isochrone", "fit_sersic_morphology", "pulsar_derived_quantities",
    "x_ray_spectral_fit", "fit_rv_orbit",
})


def test_every_real_tool_appears_in_a_manifest_or_retained_set():
    """Every TOOLS entry must be classified — core, cosmology, or the explicitly
    documented retained-but-unmanifested set above. Catches NEW tools added to
    ai_tools.py that nobody mapped (they'd land outside both sets)."""
    real_tools = _tool_names_in_ai_tools()
    core_yaml = _load_yaml(_PROMPTS_ROOT / "core" / "infrastructure.yaml")
    classified = set(core_yaml.get("tools") or [])

    for _, manifest, _ in _module_manifests():
        classified.update(manifest.get("tools") or [])

    unclassified = real_tools - classified - _RETAINED_UNMANIFESTED_TOOLS
    assert not unclassified, (
        f"Tools in ai_tools.TOOLS not classified in any manifest: {sorted(unclassified)}. "
        f"Add them to core/infrastructure.yaml, modules/<m>/manifest.yaml, or "
        f"(if intentionally dormant/shared) _RETAINED_UNMANIFESTED_TOOLS."
    )
