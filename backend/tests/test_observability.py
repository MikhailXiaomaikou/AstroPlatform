"""Tests for the stdlib metrics registry and /metrics endpoint."""

from app.observability import metrics as m


def test_counter_accumulates():
    reg = m.MetricsRegistry()
    reg.record_counter("c1", 1)
    reg.record_counter("c1", 2)
    reg.record_counter("c1", 1, source="gaia")
    snap = reg.snapshot()
    assert snap["counters"]["c1"][()] == 3
    assert snap["counters"]["c1"][(("source", "gaia"),)] == 1


def test_histogram_bucket_counts():
    reg = m.MetricsRegistry()
    for v in (0.001, 0.02, 0.3, 1.5, 8.0):
        reg.record_histogram("h1", v)
    snap = reg.snapshot()
    entry = snap["histograms"]["h1"][()]
    assert entry["count"] == 5
    assert abs(entry["sum"] - (0.001 + 0.02 + 0.3 + 1.5 + 8.0)) < 1e-9
    # 0.001 ≤ 0.005 so bucket[0] ≥ 1; all five values ≤ +Inf so bucket[-1] == 5
    assert entry["buckets"][0] >= 1
    assert entry["buckets"][-1] == 5


def test_render_prometheus_format():
    reg = m.MetricsRegistry()
    reg.record_counter("my_counter", 7, source="sdss")
    reg.record_histogram("my_hist_seconds", 0.42, source="sdss")
    text = m.render_prometheus(reg)
    assert "# TYPE my_counter counter" in text
    assert 'my_counter{source="sdss"} 7' in text
    assert "# TYPE my_hist_seconds histogram" in text
    assert 'my_hist_seconds_bucket{source="sdss",le="1.0"}' in text
    assert 'my_hist_seconds_sum{source="sdss"} 0.42' in text
    assert 'my_hist_seconds_count{source="sdss"} 1' in text


def test_render_empty_registry_has_placeholder():
    reg = m.MetricsRegistry()
    text = m.render_prometheus(reg)
    assert "empty_registry" in text


def test_label_escaping():
    reg = m.MetricsRegistry()
    reg.record_counter("x", 1, note='with "quotes" and \\ backslash')
    text = m.render_prometheus(reg)
    assert "\\\"quotes\\\"" in text
    assert "\\\\" in text
