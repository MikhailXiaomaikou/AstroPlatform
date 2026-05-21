"""Tests for the step-3 adapter resolver.

Today every adapter resolves to ``None`` (TODO placeholder). These tests
lock down the resolver shape so a future PR that fills in a real Cobaya
import path either also adds a dedicated per-adapter test or removes the
adapter from the all-None lock.
"""
from __future__ import annotations

from app.services.cobaya_adapter_registry import (
    ADAPTER_TO_COBAYA,
    EXTERNAL_PREFIX,
    GAUSSIAN_PREFIX,
    is_translation_pending,
    resolve,
)


def test_resolve_handles_empty_and_non_string_inputs():
    assert resolve(None) is None
    assert resolve("") is None
    assert resolve(123) is None  # type: ignore[arg-type]


def test_resolve_unknown_external_adapter_returns_none():
    assert resolve("external:not_a_real_adapter") is None


def test_every_registered_adapter_is_a_todo_today():
    """Every adapter in ADAPTER_TO_COBAYA is a TODO placeholder today.

    When a future PR fills in a real Cobaya import path for one of these,
    this test will fail. The fix is to (a) add a dedicated per-adapter
    test asserting the new mapping, and (b) remove that adapter from the
    all-None lock here.
    """
    for adapter, target in ADAPTER_TO_COBAYA.items():
        assert target is None, (
            f"Adapter {adapter!r} now resolves to {target!r}. Add a dedicated "
            "resolve test for it and exclude it from this all-None lock."
        )
        assert resolve(adapter) is None


def test_resolve_gaussian_prefix_returns_as_is():
    """Gaussian compressed-prior adapters pass through verbatim so the
    runner can detect them by prefix. Whether they are *translatable* is a
    separate concern owned by cobaya_runner."""
    adapter = "gaussian:H0=73.04,sigma=1.04"
    assert resolve(adapter) == adapter


def test_resolve_ignores_non_external_non_gaussian_prefix():
    assert resolve("cobaya:bao.sixdf_2011") is None
    assert resolve("bao.sixdf_2011") is None  # no prefix at all


def test_is_translation_pending_distinguishes_known_todo_from_unknown():
    # Registered adapter with TODO target -> pending.
    assert is_translation_pending("external:bao.sdss_6df_legacy") is True
    # Adapter not in the registry -> not pending (it's just unknown).
    assert is_translation_pending("external:not_a_real_adapter") is False
    # Non-external prefix -> not pending (translation is unblocked or N/A).
    assert is_translation_pending("gaussian:H0=73") is False
    # Empty / None -> not pending.
    assert is_translation_pending(None) is False
    assert is_translation_pending("") is False


def test_every_dataset_entry_external_adapter_is_registered():
    """Every external:* adapter referenced by a CosmologyDatasetEntry MUST
    have an entry in ADAPTER_TO_COBAYA. Missing entries would cause the
    runner to emit 'Unknown adapter' instead of the structured
    'translation pending' envelope when that dataset is selected."""
    from app.services.cosmology_likelihoods import _REGISTRY

    registered_adapters: set[str] = set()
    for entry in _REGISTRY.values():
        adapter = getattr(entry, "cobaya_likelihood", None)
        if isinstance(adapter, str) and adapter.startswith(EXTERNAL_PREFIX):
            registered_adapters.add(adapter)

    missing = registered_adapters - set(ADAPTER_TO_COBAYA)
    assert not missing, (
        "cosmology_likelihoods uses adapter names that are not in "
        f"cobaya_adapter_registry.ADAPTER_TO_COBAYA: {sorted(missing)}. "
        "Add them with value=None and a TODO comment so the runner emits "
        "a structured 'translation pending' envelope rather than 'unknown "
        "adapter'."
    )


def test_prefix_constants_are_canonical():
    """Lock the prefix strings so renames break tests instead of silently
    failing at runtime (e.g. if EXTERNAL_PREFIX changes, the dispatcher
    check in cobaya_runner would still pass but stop matching real
    adapters)."""
    assert EXTERNAL_PREFIX == "external:"
    assert GAUSSIAN_PREFIX == "gaussian:"
