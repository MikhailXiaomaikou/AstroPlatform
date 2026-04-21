"""R1.1 回归测试: download_and_clean_lightcurve 透传 sector / author.

背景: Round 8 报告 Paper 4 HD 189733b 卡死, 因为 helper 不接 sector kwarg.
锁定后续不能再回退。
"""

from unittest.mock import MagicMock, patch

import pytest


class _FakeCollection:
    def __init__(self, n_segments=1):
        self._n = n_segments

    def __len__(self):
        return self._n

    def stitch(self):
        return _FakeLC()


class _FakeLC:
    @property
    def time(self):
        m = MagicMock()
        m.value.tolist.return_value = [0.0, 0.1, 0.2]
        return m

    @property
    def flux(self):
        m = MagicMock()
        m.value.tolist.return_value = [1.0, 1.0, 1.0]
        return m

    @property
    def flux_err(self):
        m = MagicMock()
        m.value.tolist.return_value = [0.01, 0.01, 0.01]
        return m

    def remove_outliers(self):
        return self

    def flatten(self):
        return self


class _FakeSearch:
    def __init__(self):
        self._collection = _FakeCollection(n_segments=1)

    def __len__(self):
        return 1

    def download_all(self):
        return self._collection


def _patch_lightkurve(captured_kwargs: dict):
    """Return a mock lk module that records search_lightcurve kwargs."""
    fake_lk = MagicMock()

    def fake_search(target, **kwargs):
        captured_kwargs.clear()
        captured_kwargs["target"] = target
        captured_kwargs.update(kwargs)
        return _FakeSearch()

    fake_lk.search_lightcurve.side_effect = fake_search
    return fake_lk


def test_sector_kwarg_forwarded_to_lightkurve():
    from app.services import astro_analysis

    captured: dict = {}
    fake_lk = _patch_lightkurve(captured)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=41
        )

    assert captured["mission"] == "tess"
    assert captured["sector"] == 41
    assert "author" not in captured  # 不传 author 时不应出现在 kwargs
    assert result["meta"]["sector"] == 41


def test_author_kwarg_forwarded_to_lightkurve():
    from app.services import astro_analysis

    captured: dict = {}
    fake_lk = _patch_lightkurve(captured)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=[41, 42], author="SPOC"
        )

    assert captured["sector"] == [41, 42]
    assert captured["author"] == "SPOC"


def test_default_call_does_not_pass_sector_or_author():
    """老脚本兼容性: 不传 sector/author 时不应注入这两个 kwarg."""
    from app.services import astro_analysis

    captured: dict = {}
    fake_lk = _patch_lightkurve(captured)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        astro_analysis.download_and_clean_lightcurve("Kepler-10")

    assert captured["mission"] == "kepler"
    assert "sector" not in captured
    assert "author" not in captured


def test_empty_search_raises_informative_error():
    from app.services import astro_analysis

    fake_lk = MagicMock()
    empty_search = MagicMock()
    empty_search.__len__.return_value = 0
    fake_lk.search_lightcurve.return_value = empty_search

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        with pytest.raises(ValueError, match="No tess light curves found"):
            astro_analysis.download_and_clean_lightcurve(
                "NonExistent", mission="tess"
            )


# ── S2 (PART S): OOM guard ──────────────────────────────────────────────


class _SearchSlice:
    """SearchResult slicing 的 fake 实现, 支持 search[-3:]."""

    def __init__(self, n):
        self._n = n

    def __len__(self):
        return self._n

    def __getitem__(self, key):
        if isinstance(key, slice):
            # slice 后返回一个新的, 长度为 stop-start 的 SearchSlice
            start = key.start if key.start is not None else 0
            stop = key.stop if key.stop is not None else self._n
            if start < 0:
                start = max(0, self._n + start)
            if stop < 0:
                stop = max(0, self._n + stop)
            return _SearchSlice(max(0, stop - start))
        raise TypeError(f"Unsupported index: {key!r}")

    def download_all(self):
        return _FakeCollection(n_segments=self._n)


def test_sector_none_caps_at_default_max_segments():
    """sector=None + 14 个 TESS sector → 默认只下最近 3 个, meta 有 warning."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(14)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess"
        )

    # 应当截到默认 3 个
    assert result["meta"]["segments"] == 3
    assert result["meta"]["segments_requested"] == 14
    assert "warning" in result["meta"]
    assert "14" in result["meta"]["warning"]


def test_explicit_sector_skips_cap():
    """sector=[41, 54, 81] 显式传时不触发截断."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(3)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=[41, 54, 81]
        )

    # 没 warning 因为用户显式传了 sector
    assert "warning" not in result["meta"]
    assert result["meta"]["sector"] == [41, 54, 81]


def test_max_segments_none_disables_cap():
    """max_segments=None 显式关闭截断."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(10)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "Kepler-10", mission="kepler", max_segments=None
        )

    assert result["meta"]["segments"] == 10
    assert "warning" not in result["meta"]


def test_segments_below_cap_no_warning():
    """只有 2 个 segment 时不该触发截断提示."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(2)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess"
        )

    assert result["meta"]["segments"] == 2
    assert "warning" not in result["meta"]
