"""Observability primitives (stdlib-only MVP).

Provides:
- Structured JSON logging via `get_logger()` → JSON-formatted records
- In-memory metrics registry (counters, histograms) exposed at /metrics
  in Prometheus text-format exposition

No hard dependency on OpenTelemetry or the prometheus-client package.
When those are installed and wanted, swap the exporters here — callers
use the high-level API (record_counter, record_histogram, span).
"""

from app.observability.metrics import (
    MetricsRegistry,
    get_registry,
    record_counter,
    record_histogram,
    render_prometheus,
)

__all__ = [
    "MetricsRegistry",
    "get_registry",
    "record_counter",
    "record_histogram",
    "render_prometheus",
]
