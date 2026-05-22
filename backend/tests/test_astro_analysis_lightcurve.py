"""R1.1 regression test: download_and_clean_lightcurve passes through sector / author.

Background: Round 8 report Paper 4 HD 189733b stalled because the helper did not
accept the sector kwarg. This locks the fix against future regression.
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
        # R11-NEW-2: new homogenize loop needs to iterate the collection. Returns a stub
        # segment object (no quality column, cast is skipped).
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
    assert "author" not in captured  # author should not appear in kwargs when not passed
    assert result["meta"]["sector"] == 41


def test_download_and_clean_lightcurve_returns_numeric_arrays():
    """R18: downstream fits inside run_python typically expect ndarray, especially flux_err."""
    from app.services import astro_analysis

    captured: dict = {}
    fake_lk = _patch_lightkurve(captured)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve("HD 189733", mission="tess")

    assert isinstance(result["time"], np.ndarray)
    assert isinstance(result["flux"], np.ndarray)
    assert isinstance(result["flux_err"], np.ndarray)
    assert result["flux_err"].dtype.kind == "f"


def test_phase_fold_result_exposes_flux_alias():
    """R21: AI commonly writes folded.flux; it should be equivalent to folded.flux_folded."""
    from app.services import astro_analysis

    folded = astro_analysis.phase_fold([0.2, 0.1], [1.2, 1.1], period=1.0, t0=0.0)

    assert np.allclose(folded.flux, folded.flux_folded)
    assert np.allclose(folded["flux"], folded["flux_folded"])
    assert "flux" in folded


def test_search_lightcurve_serializes_list_cells_as_lists():
    """R21: lightkurve/astropy list cells must not be serialized by str() into \"['...']\"."""
    from app.services import astro_analysis

    class _Cell:
        def __init__(self, value):
            self._value = value

        def tolist(self):
            return self._value

    class _Row:
        mission = _Cell(["TESS Sector 41"])
        target_name = _Cell(["HD 189733"])
        exptime = _Cell([120.0])

    class _Search:
        def __len__(self):
            return 1

        def __getitem__(self, key):
            if isinstance(key, slice):
                return [_Row()]
            raise TypeError(key)

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _Search()

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.search_lightcurve("HD 189733", mission="tess")

    row = result["results"][0]
    assert row["mission"] == ["TESS Sector 41"]
    assert row["target"] == ["HD 189733"]
    assert row["exptime"] == [120.0]


def test_pro_fit_transit_returns_stable_schema_and_radius_ratio():
    """R21: pro_fit_transit must expose a stable schema to the AI and derive a reasonable Rp/Rs from box depth."""
    from app.services import astro_analysis

    t = np.linspace(0, 10, 600)
    period = 2.0
    t0 = 1.0
    rp_true = 0.15
    phase = ((t - t0) / period) % 1.0
    in_transit = (phase < 0.03) | (phase > 0.97)
    flux = np.ones_like(t)
    flux[in_transit] -= rp_true ** 2

    result = astro_analysis.pro_fit_transit(
        t.tolist(),
        flux.tolist(),
        period=period,
        t0=t0,
        rp_rs=0.1,
        a_rs=10.0,
        inc=89.0,
    )

    for key in ("rp_rs", "a_rs", "inc", "t0", "period", "chi2", "chi2_reduced", "residuals"):
        assert key in result
    assert isinstance(result["residuals"], dict)
    assert "values" in result["residuals"]
    assert 0.10 < result["rp_rs"] < 0.20


def test_download_and_clean_lightcurve_downsamples_large_arrays():
    """R18-NEW-4: large TESS light curves must be downsampled before returning to avoid OOM in downstream plots."""
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
    """R18: corrupted FITS cache can contaminate subsequent lightkurve downloads; only remove files that cannot be opened."""
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
    """Legacy script compatibility: when sector/author are not passed, neither kwarg should be injected."""
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
    """Fake implementation of SearchResult slicing, supports search[-3:]."""

    def __init__(self, n):
        self._n = n

    def __len__(self):
        return self._n

    def __getitem__(self, key):
        if isinstance(key, slice):
            # after slicing, return a new SearchSlice of length stop-start
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
    """sector=None + 14 TESS sectors → defaults to downloading only the most recent 1, meta has warning."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(14)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess"
        )

    # should be capped to the default of 1 segment
    assert result["meta"]["segments"] == 1
    assert result["meta"]["segments_requested"] == 14
    assert "warning" in result["meta"]
    assert "14" in result["meta"]["warning"]


def test_explicit_sector_skips_cap():
    """sector=[41, 54, 81] explicitly passed should not trigger the cap."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(3)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=[41, 54, 81]
        )

    # no warning because user explicitly passed sector
    assert "warning" not in result["meta"]
    assert result["meta"]["sector"] == [41, 54, 81]


def test_max_segments_none_disables_cap():
    """max_segments=None explicitly disables the cap."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(10)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "Kepler-10", mission="kepler", max_segments=None
        )

    assert result["meta"]["segments"] == 10
    assert "warning" not in result["meta"]


def test_segments_below_explicit_cap_no_warning():
    """With explicit max_segments=3, only 2 segments should not trigger a cap warning."""
    from app.services import astro_analysis

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchSlice(2)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", max_segments=3
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
    """Return value of download_all(), supports iteration + stitch."""

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
        # check whether all segments have a consistent quality dtype
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
    """Round 11 real bug: mixed int32 + str32 quality columns must be coerced to int32."""
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

    # stitch() should be called and succeed (because homogenize casts str32 to int32)
    assert mixed.stitch_was_called, "stitch() should succeed after homogenization"
    # meta should record the cast action
    assert "warning" in result["meta"]
    warn = result["meta"]["warning"]
    assert "Homogenized quality" in warn or "cast" in warn.lower()


def test_quality_column_already_int_skipped():
    """When quality column is all int, there should be no homogenize warning."""
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
    """If stitch() still raises after homogenization → fall back to the first segment, do not fail entirely."""
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
    coll._segs = [_FakeLC()]  # replace with an object usable as a single-segment lc

    fake_lk = MagicMock()
    fake_lk.search_lightcurve.return_value = _SearchFail(coll)

    with patch.dict("sys.modules", {"lightkurve": fake_lk}):
        result = astro_analysis.download_and_clean_lightcurve(
            "HD 189733", mission="tess", sector=41
        )

    # should get time/flux (from segment[0]), and meta should indicate that stitch failed
    assert "time" in result
    assert "flux" in result
    assert "warning" in result["meta"]
    assert "stitch" in result["meta"]["warning"].lower()
