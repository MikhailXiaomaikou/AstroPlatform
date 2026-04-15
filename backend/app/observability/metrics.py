"""Stdlib-only metrics registry with Prometheus text-format exposition.

Designed for low-volume observability on a single FastAPI process.
Not a replacement for prometheus-client at scale — but it lets us ship
a /metrics endpoint and structured counters without adding a dependency.

Thread-safe via a single RLock. Labels are stored as tuples so histogram
buckets across label permutations stay small.

Usage:
    from app.observability import record_counter, record_histogram
    record_counter("connector_requests_total", 1, source="gaia")
    record_histogram("connector_request_duration_seconds", 0.42, source="gaia")
"""

from __future__ import annotations

import math
from threading import RLock

# Histogram bucket upper bounds (seconds) — matches the de-facto
# Prometheus default from prometheus_client.Histogram.DEFAULT_BUCKETS.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, math.inf,
)


class MetricsRegistry:
    def __init__(self):
        self._lock = RLock()
        # counter name -> {labels_tuple -> float}
        self._counters: dict[str, dict[tuple, float]] = {}
        # histogram name -> {labels_tuple -> {"buckets": [counts], "sum": float, "count": int}}
        self._histograms: dict[str, dict[tuple, dict]] = {}

    @staticmethod
    def _labels_key(labels: dict[str, str] | None) -> tuple:
        if not labels:
            return ()
        return tuple(sorted(labels.items()))

    def record_counter(self, name: str, value: float = 1.0, **labels) -> None:
        key = self._labels_key(labels)
        with self._lock:
            bucket = self._counters.setdefault(name, {})
            bucket[key] = bucket.get(key, 0.0) + value

    def record_histogram(self, name: str, value: float, **labels) -> None:
        key = self._labels_key(labels)
        with self._lock:
            by_label = self._histograms.setdefault(name, {})
            entry = by_label.get(key)
            if entry is None:
                entry = {
                    "buckets": [0] * len(DEFAULT_BUCKETS),
                    "sum": 0.0,
                    "count": 0,
                }
                by_label[key] = entry
            for i, upper in enumerate(DEFAULT_BUCKETS):
                if value <= upper:
                    entry["buckets"][i] += 1
            entry["sum"] += value
            entry["count"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": {
                    name: {k: v for k, v in labelmap.items()}
                    for name, labelmap in self._counters.items()
                },
                "histograms": {
                    name: {
                        k: {
                            "buckets": list(entry["buckets"]),
                            "sum": entry["sum"],
                            "count": entry["count"],
                        }
                        for k, entry in labelmap.items()
                    }
                    for name, labelmap in self._histograms.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    return _registry


def record_counter(name: str, value: float = 1.0, **labels) -> None:
    _registry.record_counter(name, value, **labels)


def record_histogram(name: str, value: float, **labels) -> None:
    _registry.record_histogram(name, value, **labels)


def _format_labels(labels_tuple: tuple) -> str:
    if not labels_tuple:
        return ""
    parts = [f'{k}="{_escape(v)}"' for k, v in labels_tuple]
    return "{" + ",".join(parts) + "}"


def _escape(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus(registry: MetricsRegistry | None = None) -> str:
    """Emit Prometheus text-format exposition (OpenMetrics 0.0.4 subset)."""
    reg = registry or _registry
    snap = reg.snapshot()
    lines: list[str] = []

    for name, labelmap in sorted(snap["counters"].items()):
        lines.append(f"# HELP {name} counter")
        lines.append(f"# TYPE {name} counter")
        for labels_tuple, value in labelmap.items():
            lines.append(f"{name}{_format_labels(labels_tuple)} {value}")

    for name, labelmap in sorted(snap["histograms"].items()):
        lines.append(f"# HELP {name} histogram")
        lines.append(f"# TYPE {name} histogram")
        for labels_tuple, entry in labelmap.items():
            base_labels_str = _format_labels(labels_tuple)
            for upper, count in zip(DEFAULT_BUCKETS, entry["buckets"]):
                upper_str = "+Inf" if upper == math.inf else f"{upper}"
                # Merge bucket le="..." with any existing labels
                if labels_tuple:
                    bucket_pairs = list(labels_tuple) + [("le", upper_str)]
                    bucket_labels_str = _format_labels(tuple(bucket_pairs))
                else:
                    bucket_labels_str = f'{{le="{upper_str}"}}'
                lines.append(f"{name}_bucket{bucket_labels_str} {count}")
            lines.append(f"{name}_sum{base_labels_str} {entry['sum']}")
            lines.append(f"{name}_count{base_labels_str} {entry['count']}")

    if not lines:
        lines.append("# HELP empty_registry placeholder")
        lines.append("# TYPE empty_registry gauge")
        lines.append("empty_registry 0")

    lines.append("")  # trailing newline
    return "\n".join(lines)
