"""Modular prompt loader — assembles SYSTEM_PROMPT + allowlist from prompts/.

Reads backend/app/prompts/ three-layer structure:

    prompts/
    ├── base.md                            # 跨模块永久加载
    ├── core/
    │   ├── infrastructure.md              # 跨模块基础设施
    │   └── infrastructure.yaml            # core 工具清单
    └── modules/
        ├── cosmology/                     # active under ASTRO_RESEARCH_FOCUS=cosmology
        │   ├── prompt.md
        │   └── manifest.yaml
        └── _dormant_*/                    # 不暴露
            ├── prompt.md
            └── manifest.yaml

Per the modular-platform strategy (2026-05-10 user decision),
``_active_modules`` is hardcoded — no generic ModuleRegistry until
M5+ (second active module). Three similar usages trigger the abstraction,
not before.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _module_dir(name: str) -> Path:
    """Locate a module dir, accepting both active and _dormant_-prefixed names."""
    modules_dir = _PROMPTS_ROOT / "modules"
    for candidate in (modules_dir / name, modules_dir / f"_dormant_{name}"):
        if candidate.is_dir():
            return candidate
    raise ValueError(f"Module not found: {name!r}")


def _active_module_names(focus: str) -> list[str]:
    """Return active module names for a given ASTRO_RESEARCH_FOCUS.

    Hardcoded(Karpathy 三相似才抽象 — 等第 3 个 active 模块再引入 ModuleRegistry):
      - ``cosmology`` → ``["cosmology"]``
      - ``solar_system`` → ``["solar_system"]`` (M0 2026-05-18)
      - ``all`` / unknown / empty → 所有模块(active + 历史 _dormant_)
    """
    if focus == "cosmology":
        return ["cosmology"]
    if focus == "solar_system":
        return ["solar_system"]
    # "all" / unknown / empty → load everything (no focus gating).
    # 包含 active 模块(无 _dormant_ 前缀)和 dormant 模块(有 _dormant_ 前缀)。
    modules_dir = _PROMPTS_ROOT / "modules"
    seen: set[str] = set()
    active: list[str] = []
    for sub in sorted(modules_dir.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name.startswith("_dormant_"):
            name = sub.name[len("_dormant_") :]
        elif (sub / "manifest.yaml").exists():
            name = sub.name
        else:
            continue
        if name not in seen:
            seen.add(name)
            active.append(name)
    return active


@lru_cache(maxsize=8)
def build_system_prompt(focus: str) -> str:
    """Assemble SYSTEM_PROMPT from base + core + active module prompts.

    Resolves ``__ARCHIVE_MANIFEST__`` placeholder (chat.py legacy
    post-processing). Cached per focus — reload backend to pick up
    prompt.md edits during development.
    """
    base = _read_text(_PROMPTS_ROOT / "base.md")
    core = _read_text(_PROMPTS_ROOT / "core" / "infrastructure.md")

    module_texts: list[str] = []
    for name in _active_module_names(focus):
        mdir = _module_dir(name)
        module_texts.append(_read_text(mdir / "prompt.md"))

    text = base + "\n\n" + core + "\n\n" + "\n\n".join(module_texts)

    # Focus-specific appendix appended at the END (legacy L4 behavior:
    # "the LLM should see this last"). Loaded for any active-module focus
    # whose dir contains an appendix.md.  M0 2026-05-18: extended from
    # cosmology-only to support solar_system.
    if focus in {"cosmology", "solar_system"}:
        appendix_path = _PROMPTS_ROOT / "modules" / focus / "appendix.md"
        if appendix_path.exists():
            text = text + "\n\n" + _read_text(appendix_path)

    # Legacy __ARCHIVE_MANIFEST__ placeholder resolution (chat.py R17).
    try:
        from app.archive_versions import archive_manifest_text as _archive_mf

        text = text.replace("__ARCHIVE_MANIFEST__", _archive_mf())
    except Exception:
        text = text.replace("__ARCHIVE_MANIFEST__", "gaia=DR3, sdss=DR18")

    return text


@lru_cache(maxsize=8)
def build_allowed_tools(focus: str) -> frozenset[str]:
    """Compose the LLM-visible tool allowlist for a focus.

    = core/infrastructure.yaml tools ∪ ⋃(m.manifest.tools for m in active_modules)

    Used by chat.py ``_filter_tools_by_research_focus`` to gate which TOOLS
    the LLM can see.
    """
    core_yaml = _read_yaml(_PROMPTS_ROOT / "core" / "infrastructure.yaml")
    tools: set[str] = set(core_yaml.get("tools") or [])

    for name in _active_module_names(focus):
        mdir = _module_dir(name)
        manifest = _read_yaml(mdir / "manifest.yaml")
        manifest_tools = manifest.get("tools") or []
        if manifest_tools:
            tools.update(manifest_tools)

    return frozenset(tools)


def list_all_modules() -> list[dict]:
    """Enumerate every module manifest (active + dormant) for debugging /
    introspection. Not used at chat runtime."""
    modules_dir = _PROMPTS_ROOT / "modules"
    out: list[dict] = []
    for sub in sorted(modules_dir.iterdir()):
        if not sub.is_dir():
            continue
        manifest_path = sub / "manifest.yaml"
        if not manifest_path.exists():
            continue
        manifest = _read_yaml(manifest_path)
        out.append(
            {
                "dir": sub.name,
                "name": manifest.get("name"),
                "display": manifest.get("display"),
                "status": manifest.get("status"),
                "tools_count": len(manifest.get("tools") or []),
            }
        )
    return out
