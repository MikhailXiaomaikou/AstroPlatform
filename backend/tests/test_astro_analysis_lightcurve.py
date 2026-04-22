"""R1.1 回归测试: download_and_clean_lightcurve 透传 sector / author.

背景: Round 8 报告 Paper 4 HD 189733b 卡死, 因为 helper 不接 sector kwarg.
锁定后续不能再回退。
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


class _FakeCollection:
    def __init__(self, n_segments=1):
        self._n = n_segments

    def __len__(self):
        return self._n

    def __iter__(self):
        # R11-NEW-2: 新的 homogenize 循环要 iterate collection. 返回 stub
        # segment 对象 (无 quality 列, 跳过 cast).
        class _Stub:
            columns = {"time": True}
            def __contains__(_self, key):
                return False
            def __getitem__(_self, key):
                raise KeyError(key)
            def __setitem__(_self, key, value):
                pass
            def remove_column(_self, name):
                pass
        return iter([_Stub() for _ in range(self._n)])

    def __getitem__(self, i):
        class _SingleSegLC:
            """Fake single-segment lightcurve for stitch-fallback path."""
            @property
            def time(_self):
                m = MagicMock()
                m.value.tolist.return_value = [0.0, 0.1]
                return m
            @property
            def flux(_self):
                m = MagicMock()
                m.value.tolist.return_value = [1.0, 1.0]
                return m
            @property
            def flux_err(_self):
                return None
            def remove_outliers(_self): return _self
            def flatten(_self): return _self
        return _SingleSegLC()

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


def test_download_and_clean_lightcurve_returns_numeric_arrays():
    """R18: run_python 里下游拟合通常期望 ndarray, 尤其 flux_err。"""
    from app.services import astro_analysis

    captured: dict = {}
    fake_lk = _patch_lightkurve(captured)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve("HD 189733", mission="tess")

    assert isinstance(result["time"], np.ndarray)
    assert isinstance(result["flux"], np.ndarray)
    assert isinstance(result["flux_err"], np.ndarray)
    assert result["flux_err"].dtype.kind == "f"


def test_download_and_clean_lightcurve_downsamples_large_arrays():
    """R18-NEW-4: 大 TESS 曲线返回前要降采样，避免后续绘图 OOM。"""
    from app.services import astro_analysis

    class _Quantity:
        def __init__(self, value):
            self.value = value

    class _LargeLC:
        def __init__(self):
            self._time = np.arange(1000, dtype=float)
            self._flux = np.ones(1000, dtype=float)
            self._flux_err = np.full(1000, 0.01, dtype=float)

        @property
        def time(self):
            return _Quantity(self._time)

        @property
        def flux(self):
            return _Quantity(self._flux)

        @property
        def flux_err(self):
            return _Quantity(self._flux_err)

        def remove_outliers(self):
            return self

        def flatten(self):
            return self

    class _LargeCollection(_FakeCollection):
        def stitch(self):
            return _LargeLC()

    class _LargeSearch:
        def __len__(self):
            return 1

        def download_all(self):
            return _LargeCollection(n_segments=1)

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _LargeSearch()

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", max_points=100
        )

    assert len(result["time"]) <= 100
    assert len(result["flux"]) == len(result["time"])
    assert len(result["flux_err"]) == len(result["time"])
    assert result["meta"]["points_original"] == 1000
    assert result["meta"]["points_returned"] <= 100
    assert "Downsampled light curve" in result["meta"]["warning"]


def test_cleanup_lightkurve_cache_removes_only_corrupted_fits(tmp_path):
    """R18: 坏 FITS 缓存会污染后续 lightkurve 下载；只删打不开的文件。"""
    from astropy.io import fits
    from app.services import astro_analysis

    good = tmp_path / "good.fits"
    bad = tmp_path / "bad.fits"
    fits.PrimaryHDU().writeto(good)
    bad.write_text("not a fits file", encoding="utf-8")

    result = astro_analysis.cleanup_lightkurve_cache(tmp_path)

    assert result["checked"] == 2
    assert good.exists()
    assert not bad.exists()
    assert str(bad) in result["removed"]


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


# ── R11-NEW-2 (PART V): TableMergeError vstack dtype homogenize ──


class _SegmentWithQuality:
    """Fake TESS segment exposing a quality column with configurable dtype."""

    def __init__(self, quality_dtype="int32", has_quality=True):
        import numpy as np
        self._quality = np.array(
            ["0", "0", "1"] if quality_dtype.startswith("str") else [0, 0, 1],
            dtype=quality_dtype,
        ) if has_quality else None
        self.columns = {"quality": True, "time": True} if has_quality else {"time": True}
        self.removed = False

    def __contains__(self, key):
        return key in self.columns

    def __getitem__(self, key):
        if key == "quality":
            class _Col:
                dtype = self._quality.dtype
                def __array__(_self, dtype=None):
                    return self._quality
            return _Col()
        raise KeyError(key)

    def __setitem__(self, key, value):
        if key == "quality":
            import numpy as np
            self._quality = np.asarray(value)
            self.columns[key] = True

    def remove_column(self, name):
        if name in self.columns:
            del self.columns[name]
            self.removed = True


class _CollectionWithMixedQuality:
    """download_all() 的返回值, 支持 iteration + stitch."""

    def __init__(self, segments):
        self._segs = segments
        self.stitch_was_called = False

    def __iter__(self):
        return iter(self._segs)

    def __len__(self):
        return len(self._segs)

    def __getitem__(self, i):
        return self._segs[i]

    def stitch(self):
        # 检查所有 segment 的 quality dtype 是否一致
        import numpy as np
        dtypes = set()
        for s in self._segs:
            if "quality" in s:
                dtypes.add(s["quality"].dtype.kind)
        if len(dtypes) > 1:
            from astropy.utils.exceptions import AstropyUserWarning  # noqa
            class _TableMergeError(Exception):
                pass
            raise _TableMergeError(
                "The 'quality' columns have incompatible types: "
                f"{sorted(dtypes)}"
            )
        self.stitch_was_called = True
        return _FakeLC()


class _SearchReturningMixed:
    def __init__(self, mixed_collection):
        self._mc = mixed_collection

    def __len__(self):
        return len(self._mc)

    def __getitem__(self, key):
        return _SearchReturningMixed(self._mc)  # slicing keeps everything

    def download_all(self):
        return self._mc


def test_mixed_quality_dtype_homogenized_before_stitch():
    """Round 11 真 bug: 混合 int32 + str32 quality 列应被强制统一成 int32."""
    from app.services import astro_analysis

    segments = [
        _SegmentWithQuality("int32"),
        _SegmentWithQuality("int32"),
        _SegmentWithQuality("<U32"),  # str32 equivalent
    ]
    mixed = _CollectionWithMixedQuality(segments)

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchReturningMixed(mixed)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=41
        )

    # stitch() 应当被调, 且成功 (因为 homogenize 把 str32 cast 成 int32)
    assert mixed.stitch_was_called, "stitch() should succeed after homogenization"
    # meta 里应记录 cast 动作
    assert "warning" in result["meta"]
    warn = result["meta"]["warning"]
    assert "Homogenized quality" in warn or "cast" in warn.lower()


def test_quality_column_already_int_skipped():
    """quality 列全是 int 时不该有 homogenize warning."""
    from app.services import astro_analysis

    segments = [_SegmentWithQuality("int32") for _ in range(3)]
    mixed = _CollectionWithMixedQuality(segments)

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchReturningMixed(mixed)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=41
        )

    assert "warning" not in result["meta"] or "Homogenized" not in result["meta"].get("warning", "")
    assert mixed.stitch_was_called


def test_stitch_fallback_to_first_segment():
    """stitch() 在 homogenize 后仍然抛错 → 退到第一个 segment, 不整体失败."""
    from app.services import astro_analysis

    class _AlwaysFailColl:
        def __init__(self):
            self._segs = [_SegmentWithQuality("int32")]
        def __iter__(self): return iter(self._segs)
        def __len__(self): return len(self._segs)
        def __getitem__(self, i): return self._segs[i] if isinstance(i, int) else _FakeCollection(1)
        def stitch(self):
            raise ValueError("time column dtype mismatch")

    class _SearchFail:
        def __init__(self, mc): self._mc = mc
        def __len__(self): return 1
        def __getitem__(self, key): return self
        def download_all(self): return self._mc

    coll = _AlwaysFailColl()
    coll._segs = [_FakeLC()]  # 替换成可当 single-segment lc 的对象

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchFail(coll)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=41
        )

    # 应得到 time/flux (来自 segment[0]), 且 meta 里说明 stitch 失败
    assert "time" in result
    assert "flux" in result
    assert "warning" in result["meta"]
    assert "stitch" in result["meta"]["warning"].lower()
