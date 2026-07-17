"""Inline event-buffer flush must never take down a user request (2026-07-09).

CI regression: the 51st chat call in test_starter_daily_quota was the
FLUSH_SIZE-th buffered analytics event; EventCollector.track awaited
flush() inline with no exception guard, and flush() writes through the
app-default async_session — on a fresh database without the user_events
table the analytics write crashed the chat request itself (locally it
passed only because the dev astro.db happens to have the table).
Analytics is best-effort by contract: periodic_flush already swallows
failures, and the inline flush must do the same.
"""
from __future__ import annotations

from app.services import event_collector as ec


async def test_inline_flush_failure_does_not_propagate(monkeypatch):
    collector = ec.EventCollector(flush_size=2)

    def broken_session():
        raise RuntimeError("analytics DB unavailable")

    monkeypatch.setattr(ec, "async_session", broken_session)
    # The second track() hits FLUSH_SIZE and triggers the inline flush;
    # surviving both calls without an exception IS the regression assertion.
    await collector.track("session.started", {}, consent_verified=True)
    await collector.track("session.started", {}, consent_verified=True)
    # flush() drains the buffer before writing; the failed batch is dropped,
    # not retried forever on the request path.
    assert collector.buffer == []


async def test_flush_still_raises_for_direct_callers(monkeypatch):
    """The guard lives at the request-path call site, not inside flush():
    periodic_flush keeps its own try/except and direct callers still see
    the real error."""
    collector = ec.EventCollector(flush_size=100)

    def broken_session():
        raise RuntimeError("analytics DB unavailable")

    monkeypatch.setattr(ec, "async_session", broken_session)
    await collector.track("session.started", {}, consent_verified=True)
    try:
        await collector.flush()
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("flush() must propagate to direct callers")
