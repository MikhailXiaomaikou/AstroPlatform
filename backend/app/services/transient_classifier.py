"""Transient light curve classifier using random forest on extracted features.

Classifies astronomical transients (supernovae, TDEs, CVs, AGN, kilonovae,
long-period variables) from photometric light curve data.  The model is
trained lazily on synthetic feature distributions the first time
`classify_transient` is called, then cached for subsequent invocations.
"""

import json
import logging
from typing import Any

import numpy as np
from scipy.stats import skew
from sklearn.ensemble import RandomForestClassifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported transient classes
# ---------------------------------------------------------------------------
TRANSIENT_CLASSES = [
    "SN_Ia",
    "SN_II",
    "SN_Ib/c",
    "TDE",
    "CV",
    "AGN",
    "KN",
    "LPV",
    "Unknown",
]

# Feature names expected by the classifier (order matters for the RF).
FEATURE_NAMES = [
    "rise_time",
    "decline_rate_15",
    "decline_rate_40",
    "peak_magnitude",
    "amplitude",
    "duration_above_half",
    "skewness",
    "beyond_1std",
    "stetson_K",
    "mean_mag",
    "std_mag",
    "median_abs_dev",
]

# This classifier is a software demonstration trained entirely on generated
# feature distributions.  These thresholds prevent the demo from silently
# imputing an empty/inadequate light curve into a plausible-looking class.
MIN_LIGHT_CURVE_POINTS = 10
REQUIRED_DIRECT_FEATURES = frozenset(
    {
        "rise_time",
        "peak_magnitude",
        "amplitude",
        "duration_above_half",
        "mean_mag",
        "std_mag",
    }
)

_SYNTHETIC_MODEL_WARNING = (
    "This random-forest model was trained only on generated feature "
    "distributions. Its candidate class, score, probabilities, and feature "
    "importance are a software demonstration, not an observational "
    "classification or a calibrated scientific result."
)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def extract_lc_features(
    times: np.ndarray | list,
    mags: np.ndarray | list,
    mag_errs: np.ndarray | list | None = None,
    band: str = "g",
) -> dict[str, Any]:
    """Extract classification features from a light curve.

    Parameters
    ----------
    times : array-like
        Observation times (JD or MJD).
    mags : array-like
        Magnitudes (smaller = brighter).
    mag_errs : array-like or None
        Magnitude uncertainties.  If *None*, uniform errors of 0.1 mag are
        assumed for Stetson-K.
    band : str
        Photometric band label (for bookkeeping; does not affect computation).

    Returns
    -------
    dict
        Feature dictionary.  All values are floats or *None* for features that
        could not be computed.
    """
    try:
        times = np.asarray(times, dtype=float)
        mags = np.asarray(mags, dtype=float)
        if mag_errs is not None:
            mag_errs = np.asarray(mag_errs, dtype=float)
    except (TypeError, ValueError):
        return _invalid_features("Light-curve arrays must contain only numeric values")

    if times.ndim != 1 or mags.ndim != 1:
        return _invalid_features("Light-curve times and magnitudes must be one-dimensional arrays")
    if len(times) != len(mags):
        return _invalid_features(
            "Light-curve times and magnitudes must have the same length"
        )
    if mag_errs is not None:
        if mag_errs.ndim != 1 or len(mag_errs) != len(times):
            return _invalid_features(
                "Magnitude uncertainties must be a one-dimensional array matching the data length"
            )

    # --- strip NaN / Inf ------------------------------------------------
    valid = np.isfinite(times) & np.isfinite(mags)
    if mag_errs is not None:
        valid &= np.isfinite(mag_errs) & (mag_errs > 0)
    times = times[valid]
    mags = mags[valid]
    if mag_errs is not None:
        mag_errs = mag_errs[valid]

    n = len(times)

    # Fail closed instead of turning absent/sparse observations into a median
    # synthetic object.  Ten points is still not a scientific validation
    # threshold; it is only the minimum needed to run this demo at all.
    if n < MIN_LIGHT_CURVE_POINTS:
        logger.warning("Too few valid data points (%d) for feature extraction", n)
        return _invalid_features(
            f"At least {MIN_LIGHT_CURVE_POINTS} valid photometry points are required; got {n}",
            n_valid_points=n,
        )

    # Sort by time
    order = np.argsort(times)
    times = times[order]
    mags = mags[order]
    if mag_errs is not None:
        mag_errs = mag_errs[order]
    else:
        mag_errs = np.full(n, 0.1)

    # --- basic statistics ------------------------------------------------
    mean_mag = float(np.mean(mags))
    std_mag = float(np.std(mags, ddof=1)) if n > 1 else 0.0
    median_abs_dev = float(np.median(np.abs(mags - np.median(mags))))

    # Peak (brightest = minimum magnitude)
    peak_idx = int(np.argmin(mags))
    peak_magnitude = float(mags[peak_idx])
    amplitude = float(np.max(mags) - np.min(mags))

    # --- rise time -------------------------------------------------------
    # Time from first observation to peak.
    rise_time = float(times[peak_idx] - times[0]) if peak_idx > 0 else 0.0

    # --- decline rates ---------------------------------------------------
    # Magnitude change in the 15 and 40 days after peak.
    post_peak_times = times[peak_idx:] - times[peak_idx]
    post_peak_mags = mags[peak_idx:]

    decline_rate_15 = _decline_rate(post_peak_times, post_peak_mags, 15.0)
    decline_rate_40 = _decline_rate(post_peak_times, post_peak_mags, 40.0)

    # --- duration above half-maximum ------------------------------------
    half_max_mag = peak_magnitude + amplitude / 2.0
    above_half = mags <= half_max_mag  # brighter than half-max
    if np.any(above_half):
        duration_above_half = float(times[above_half][-1] - times[above_half][0])
    else:
        duration_above_half = 0.0

    # --- statistical features -------------------------------------------
    skewness = float(skew(mags, bias=False)) if n >= 3 else 0.0

    beyond_1std = (
        float(np.sum(np.abs(mags - mean_mag) > std_mag) / n) if std_mag > 0 else 0.0
    )

    stetson_K = _stetson_k(mags, mag_errs, mean_mag)

    # --- optional / nullable features -----------------------------------
    color_at_peak: float | None = None  # requires multi-band data
    lomb_scargle_period = _lomb_scargle_period(times, mags)

    return {
        "rise_time": rise_time,
        "decline_rate_15": decline_rate_15,
        "decline_rate_40": decline_rate_40,
        "peak_magnitude": peak_magnitude,
        "amplitude": amplitude,
        "duration_above_half": duration_above_half,
        "color_at_peak": color_at_peak,
        "lomb_scargle_period": lomb_scargle_period,
        "skewness": skewness,
        "beyond_1std": beyond_1std,
        "stetson_K": stetson_K,
        "mean_mag": mean_mag,
        "std_mag": std_mag,
        "median_abs_dev": median_abs_dev,
    }


# ---------------------------------------------------------------------------
# Helper functions for feature extraction
# ---------------------------------------------------------------------------


def _decline_rate(dt: np.ndarray, dm: np.ndarray, window: float) -> float:
    """Magnitude change within *window* days after peak."""
    mask = dt <= window
    if np.sum(mask) < 2:
        return 0.0
    # linear interpolation to the exact window boundary
    idx_within = np.where(mask)[0]
    return float(dm[idx_within[-1]] - dm[0])


def _stetson_k(mags: np.ndarray, errs: np.ndarray, mean_mag: float) -> float:
    """Stetson K variability statistic."""
    n = len(mags)
    if n < 2:
        return 0.0
    residuals = (mags - mean_mag) / errs
    abs_sum = np.sum(np.abs(residuals))
    sq_sum = np.sum(residuals**2)
    if sq_sum == 0:
        return 0.0
    return float(abs_sum / np.sqrt(n * sq_sum))


def _lomb_scargle_period(times: np.ndarray, mags: np.ndarray) -> float | None:
    """Best Lomb-Scargle period, or None if undetermined."""
    try:
        from astropy.timeseries import LombScargle

        n = len(times)
        if n < 10:
            return None

        baseline = times[-1] - times[0]
        if baseline < 1.0:
            return None

        min_freq = 1.0 / baseline
        max_freq = n / (2.0 * baseline)  # pseudo-Nyquist
        if max_freq <= min_freq:
            return None

        ls = LombScargle(times, mags)
        frequency, power = ls.autopower(
            minimum_frequency=min_freq,
            maximum_frequency=max_freq,
        )
        if len(power) == 0:
            return None

        best_freq = frequency[np.argmax(power)]
        if best_freq <= 0:
            return None

        best_period = float(1.0 / best_freq)
        # Reject period if peak power is too low (false alarm probability > 5%).
        fap = ls.false_alarm_probability(power.max())
        if fap > 0.05:
            return None
        return best_period
    except Exception:
        return None


def _empty_features() -> dict[str, Any]:
    """Return a feature dict filled with zeros / None for edge cases."""
    return {
        "rise_time": 0.0,
        "decline_rate_15": 0.0,
        "decline_rate_40": 0.0,
        "peak_magnitude": 0.0,
        "amplitude": 0.0,
        "duration_above_half": 0.0,
        "color_at_peak": None,
        "lomb_scargle_period": None,
        "skewness": 0.0,
        "beyond_1std": 0.0,
        "stetson_K": 0.0,
        "mean_mag": 0.0,
        "std_mag": 0.0,
        "median_abs_dev": 0.0,
    }


def _invalid_features(
    message: str,
    *,
    n_valid_points: int = 0,
) -> dict[str, Any]:
    """Return an explicitly invalid feature envelope for fail-closed callers."""
    return {
        **_empty_features(),
        "feature_extraction_valid": False,
        "feature_extraction_error": message,
        "n_valid_points": int(n_valid_points),
    }


# ---------------------------------------------------------------------------
# Transient Classifier
# ---------------------------------------------------------------------------


class TransientClassifier:
    """Random-forest classifier for transient light curves.

    The model is trained lazily on synthetic feature distributions the first
    time :meth:`classify_transient` is called.  The fitted model and metadata
    are cached as class attributes so subsequent calls skip training.
    """

    SUPPORTED_CLASSES: list[str] = TRANSIENT_CLASSES

    # Class-level cache (shared across instances).
    _model: RandomForestClassifier | None = None
    _feature_names: list[str] = FEATURE_NAMES
    _feature_medians: list[float] = []
    _is_trained: bool = False

    # ------------------------------------------------------------------
    # Training data generation
    # ------------------------------------------------------------------

    @classmethod
    def _generate_training_data(
        cls, n_per_class: int = 500, seed: int = 42
    ) -> tuple[np.ndarray, np.ndarray, list[str]]:
        """Generate synthetic training samples for each transient class.

        Returns (X, y_indices, label_list) where *label_list* maps integer
        labels back to class names.
        """
        rng = np.random.default_rng(seed)
        label_list = [c for c in cls.SUPPORTED_CLASSES if c != "Unknown"]
        all_X: list[np.ndarray] = []
        all_y: list[np.ndarray] = []

        for class_idx, cls_name in enumerate(label_list):
            samples = cls._generate_class_samples(cls_name, n_per_class, rng)
            all_X.append(samples)
            all_y.append(np.full(n_per_class, class_idx, dtype=int))

        X = np.vstack(all_X)
        y = np.concatenate(all_y)
        return X, y, label_list

    @classmethod
    def _generate_class_samples(
        cls, class_name: str, n: int, rng: np.random.Generator
    ) -> np.ndarray:
        """Return (n, n_features) array of synthetic features for one class.

        Feature order follows ``FEATURE_NAMES``.
        """
        # Columns: rise_time, decline_rate_15, decline_rate_40,
        #          peak_magnitude, amplitude, duration_above_half,
        #          skewness, beyond_1std, stetson_K,
        #          mean_mag, std_mag, median_abs_dev

        if class_name == "SN_Ia":
            rise = rng.uniform(15, 20, n)
            dr15 = rng.normal(1.1, 0.15, n)
            dr40 = rng.normal(2.5, 0.3, n)
            peak = rng.uniform(-19, -18, n)
            amp = rng.uniform(2.5, 4.0, n)
            dur = rng.uniform(30, 60, n)
            skw = rng.normal(-0.3, 0.3, n)
            bey = rng.uniform(0.3, 0.5, n)
            stK = rng.normal(0.8, 0.1, n)
            mean = peak + amp / 2
            std = rng.uniform(0.8, 1.5, n)
            mad = std * 0.6745

        elif class_name == "SN_II":
            rise = rng.uniform(5, 15, n)
            dr15 = rng.normal(0.3, 0.1, n)
            dr40 = rng.normal(0.8, 0.2, n)
            peak = rng.uniform(-17, -15, n)
            amp = rng.uniform(1.5, 3.5, n)
            dur = rng.uniform(60, 150, n)
            skw = rng.normal(0.2, 0.4, n)
            bey = rng.uniform(0.25, 0.45, n)
            stK = rng.normal(0.75, 0.1, n)
            mean = peak + amp / 2
            std = rng.uniform(0.5, 1.2, n)
            mad = std * 0.6745

        elif class_name == "SN_Ib/c":
            rise = rng.uniform(10, 15, n)
            dr15 = rng.normal(0.8, 0.15, n)
            dr40 = rng.normal(2.0, 0.3, n)
            peak = rng.uniform(-18, -16, n)
            amp = rng.uniform(2.0, 3.5, n)
            dur = rng.uniform(30, 70, n)
            skw = rng.normal(-0.1, 0.3, n)
            bey = rng.uniform(0.3, 0.5, n)
            stK = rng.normal(0.78, 0.1, n)
            mean = peak + amp / 2
            std = rng.uniform(0.7, 1.3, n)
            mad = std * 0.6745

        elif class_name == "TDE":
            rise = rng.uniform(20, 50, n)
            dr15 = rng.normal(0.2, 0.08, n)
            dr40 = rng.normal(0.5, 0.15, n)
            peak = rng.uniform(-20, -18, n)
            amp = rng.uniform(1.5, 3.0, n)
            dur = rng.uniform(80, 200, n)
            skw = rng.normal(0.5, 0.3, n)
            bey = rng.uniform(0.2, 0.4, n)
            stK = rng.normal(0.7, 0.1, n)
            mean = peak + amp / 2
            std = rng.uniform(0.4, 1.0, n)
            mad = std * 0.6745

        elif class_name == "CV":
            rise = rng.uniform(0.5, 2, n)
            dr15 = rng.normal(1.5, 0.4, n)
            dr40 = rng.normal(3.0, 0.6, n)
            peak = rng.uniform(-8, -4, n)
            amp = rng.uniform(2, 5, n)
            dur = rng.uniform(2, 15, n)
            skw = rng.normal(-0.8, 0.4, n)
            bey = rng.uniform(0.35, 0.55, n)
            stK = rng.normal(0.9, 0.12, n)
            mean = peak + amp / 2
            std = rng.uniform(1.0, 2.0, n)
            mad = std * 0.6745

        elif class_name == "AGN":
            rise = rng.uniform(10, 100, n)
            dr15 = rng.normal(0.1, 0.05, n)
            dr40 = rng.normal(0.2, 0.1, n)
            peak = rng.uniform(-22, -18, n)
            amp = rng.uniform(0.2, 1.0, n)
            dur = rng.uniform(100, 500, n)
            skw = rng.normal(0.0, 0.5, n)
            bey = rng.uniform(0.15, 0.35, n)
            stK = rng.normal(0.65, 0.1, n)
            mean = peak + amp / 2
            std = rng.uniform(0.1, 0.5, n)
            mad = std * 0.6745

        elif class_name == "KN":
            rise = rng.uniform(0.5, 2, n)
            dr15 = rng.normal(0.8, 0.2, n)
            dr40 = rng.normal(2.0, 0.5, n)
            peak = rng.uniform(-16, -14, n)
            amp = rng.uniform(2.0, 4.0, n)
            dur = rng.uniform(3, 15, n)
            skw = rng.normal(-0.6, 0.3, n)
            bey = rng.uniform(0.35, 0.55, n)
            stK = rng.normal(0.85, 0.1, n)
            mean = peak + amp / 2
            std = rng.uniform(0.8, 1.5, n)
            mad = std * 0.6745

        elif class_name == "LPV":
            rise = rng.uniform(50, 250, n)
            dr15 = rng.normal(0.15, 0.05, n)
            dr40 = rng.normal(0.35, 0.1, n)
            peak = rng.uniform(-6, -2, n)
            amp = rng.uniform(1, 3, n)
            dur = rng.uniform(100, 500, n)
            skw = rng.normal(0.0, 0.2, n)
            bey = rng.uniform(0.3, 0.5, n)
            stK = rng.normal(0.85, 0.08, n)
            mean = peak + amp / 2
            std = rng.uniform(0.4, 1.2, n)
            mad = std * 0.6745

        else:
            # Fallback: uniform noise
            rise = rng.uniform(0, 100, n)
            dr15 = rng.uniform(0, 2, n)
            dr40 = rng.uniform(0, 4, n)
            peak = rng.uniform(-22, -2, n)
            amp = rng.uniform(0.1, 5, n)
            dur = rng.uniform(1, 500, n)
            skw = rng.normal(0, 0.5, n)
            bey = rng.uniform(0.1, 0.6, n)
            stK = rng.uniform(0.5, 1.0, n)
            mean = peak + amp / 2
            std = rng.uniform(0.1, 2, n)
            mad = std * 0.6745

        return np.column_stack(
            [
                rise,
                dr15,
                dr40,
                peak,
                amp,
                dur,
                skw,
                bey,
                stK,
                mean,
                std,
                mad,
            ]
        )

    # ------------------------------------------------------------------
    # Model training
    # ------------------------------------------------------------------

    @classmethod
    def _train_model(cls) -> None:
        """Train the random-forest classifier on synthetic data."""
        logger.info("Training transient classifier (first call) ...")
        X, y, label_list = cls._generate_training_data(n_per_class=500)

        model = RandomForestClassifier(
            n_estimators=200,
            max_depth=12,
            min_samples_split=5,
            min_samples_leaf=5,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X, y)

        # Store per-feature medians for imputing missing values at classify time.
        cls._feature_medians = [float(np.median(X[:, i])) for i in range(X.shape[1])]

        cls._model = model
        cls._label_list = label_list
        cls._is_trained = True
        logger.info(
            "Transient classifier trained — %d classes, %d samples",
            len(label_list),
            len(y),
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    @classmethod
    def classify_transient(cls, features: dict[str, Any]) -> dict[str, Any]:
        """Classify a transient given its extracted feature dictionary.

        Parameters
        ----------
        features : dict
            Output of :func:`extract_lc_features`.

        Returns
        -------
        dict
            ``classification`` : str — predicted class label.
            ``confidence`` : float — probability of the top class.
            ``probabilities`` : dict[str, float] — per-class probabilities.
            ``feature_importance`` : dict[str, float] — RF importances.
            ``features_used`` : dict — the numeric features fed to the model.
        """
        if not isinstance(features, dict) or not features:
            return classification_failure("A non-empty feature dictionary is required")

        extraction_error = features.get("feature_extraction_error")
        if features.get("feature_extraction_valid") is False or extraction_error:
            return classification_failure(
                str(extraction_error or "Feature extraction failed")
            )

        missing_required: list[str] = []
        for name in sorted(REQUIRED_DIRECT_FEATURES):
            value = features.get(name)
            try:
                valid = value is not None and np.isfinite(float(value))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                missing_required.append(name)
        if missing_required:
            return classification_failure(
                "Missing or non-finite required classification features: "
                + ", ".join(missing_required)
            )

        # Lazy-init only after input validation so empty requests do not train
        # the synthetic model or produce a plausible median-imputed answer.
        if not cls._is_trained:
            cls._train_model()

        model = cls._model
        assert model is not None  # for type-checker

        # Build feature vector in the canonical order.
        feature_vector = []
        for i, fname in enumerate(cls._feature_names):
            val = features.get(fname)
            try:
                valid = val is not None and np.isfinite(float(val))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                val = cls._feature_medians[i]
            feature_vector.append(float(val))

        X = np.array(feature_vector).reshape(1, -1)
        probas = model.predict_proba(X)[0]
        pred_idx = int(np.argmax(probas))
        confidence = float(probas[pred_idx])
        classification = cls._label_list[pred_idx]

        # Per-class probabilities
        probabilities = {
            cls._label_list[i]: round(float(probas[i]), 4)
            for i in range(len(cls._label_list))
        }

        # Feature importances from the fitted forest.
        importances = model.feature_importances_
        feature_importance = {
            cls._feature_names[i]: round(float(importances[i]), 4)
            for i in range(len(cls._feature_names))
        }

        # If confidence is very low, flag as Unknown.
        if confidence < 0.60:
            classification = "Unknown"

        features_used = {
            fname: feature_vector[i] for i, fname in enumerate(cls._feature_names)
        }

        return {
            "__tool_status__": "SYNTHETIC",
            "__do_not_claim__": True,
            "__message_to_model__": (
                "The transient candidate class and confidence below come from "
                "a model trained entirely on generated feature distributions. "
                "You MUST NOT report either value as a scientific classification, "
                "observational conclusion, calibrated probability, or publication "
                "result. Independent validation on labelled survey data and/or "
                "spectroscopic confirmation is required."
            ),
            "data_origin": "synthetic",
            "analysis_status": "simulated_demo",
            "publication_ready": False,
            "preliminary_ready": False,
            "scientific_conclusion_ready": False,
            "classification_claimable": False,
            "confidence_calibrated": False,
            "classification": classification,
            "confidence": round(confidence, 4),
            "probabilities": probabilities,
            "feature_importance": feature_importance,
            "features_used": features_used,
            "warning": _SYNTHETIC_MODEL_WARNING,
            "warnings": [_SYNTHETIC_MODEL_WARNING],
            "model_provenance": {
                "training_data_origin": "generated_feature_distributions",
                "independent_validation": False,
                "intended_use": "software_demonstration_only",
            },
        }

    # ------------------------------------------------------------------
    # Alert-based classification
    # ------------------------------------------------------------------

    @classmethod
    def classify_from_alert(cls, alert_data: dict[str, Any]) -> dict[str, Any]:
        """Classify a transient from an alert packet.

        Expects ``alert_data`` to contain a ``raw_data`` key whose JSON
        value has a ``candidates`` list.  Each candidate dict must have at
        minimum ``jd``, ``magpsf``, ``sigmapsf``, and ``fid`` fields (ZTF
        alert schema).

        Parameters
        ----------
        alert_data : dict
            Alert record, typically a row from the ``transient_alerts`` table.

        Returns
        -------
        dict
            Classification result (same schema as :meth:`classify_transient`)
            with an extra ``alert_meta`` key.
        """
        # --- unpack raw photometry from the alert -----------------------
        raw = alert_data.get("raw_data")
        if raw is None:
            return classification_failure("No raw_data in alert")

        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return classification_failure("raw_data is not valid JSON")

        candidates = raw.get("candidates")
        if not candidates or not isinstance(candidates, list):
            return classification_failure("No candidates list in raw_data")

        times: list[float] = []
        mags: list[float] = []
        mag_errs: list[float] = []
        fids: list[int] = []

        for cand in candidates:
            jd = cand.get("jd")
            mag = cand.get("magpsf")
            err = cand.get("sigmapsf")
            fid = cand.get("fid")
            if jd is None or mag is None:
                continue
            try:
                jd_f = float(jd)
                mag_f = float(mag)
                err_f = float(err) if err is not None else 0.1
                fid_i = int(fid) if fid is not None else 1
            except (ValueError, TypeError):
                continue
            if not (np.isfinite(jd_f) and np.isfinite(mag_f)):
                continue
            times.append(jd_f)
            mags.append(mag_f)
            mag_errs.append(err_f)
            fids.append(fid_i)

        if len(times) < MIN_LIGHT_CURVE_POINTS:
            return classification_failure(
                f"Too few valid photometry points ({len(times)})"
            )

        # Prefer g-band (fid=1) if available; otherwise use all data.
        times_arr = np.array(times)
        mags_arr = np.array(mags)
        errs_arr = np.array(mag_errs)
        fids_arr = np.array(fids)

        g_mask = fids_arr == 1
        if np.sum(g_mask) >= 5:
            t_use = times_arr[g_mask]
            m_use = mags_arr[g_mask]
            e_use = errs_arr[g_mask]
            band = "g"
        elif np.sum(fids_arr == 2) >= 5:
            r_mask = fids_arr == 2
            t_use = times_arr[r_mask]
            m_use = mags_arr[r_mask]
            e_use = errs_arr[r_mask]
            band = "r"
        else:
            t_use = times_arr
            m_use = mags_arr
            e_use = errs_arr
            band = "mixed"

        features = extract_lc_features(t_use, m_use, e_use, band=band)
        result = cls.classify_transient(features)
        result["alert_meta"] = {
            "n_candidates": len(candidates),
            "n_valid_points": len(times),
            "band_used": band,
            "time_baseline_days": float(t_use[-1] - t_use[0])
            if len(t_use) > 1
            else 0.0,
            "source_id": alert_data.get("source_id"),
        }
        result["data_quality"] = {"n_photometry_points": len(t_use)}
        return result


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def classification_failure(message: str) -> dict[str, Any]:
    """Return an error-state classification result."""
    return {
        "__tool_status__": "FAILED",
        "__do_not_claim__": True,
        "__message_to_model__": (
            "Transient classification did not run because the input was invalid "
            "or insufficient. You MUST NOT infer or report a class or confidence."
        ),
        "success": False,
        "data_origin": "unavailable",
        "analysis_status": "failed",
        "publication_ready": False,
        "preliminary_ready": False,
        "scientific_conclusion_ready": False,
        "classification": "Unknown",
        "confidence": 0.0,
        "probabilities": {},
        "feature_importance": {},
        "features_used": {},
        "error": message,
    }


def classify_with_spectroscopy(wavelength: list, flux: list, z: float = 0.0) -> dict:
    """Spectroscopic transient classification using template matching.

    Matches observed spectrum against SN Ia, SN II, SN Ib/c, TDE, AGN templates
    using chi-squared cross-correlation.
    """
    import numpy as np

    wave = np.array(wavelength) / (1.0 + z)  # Rest frame
    fl = np.array(flux)

    # Normalize
    fl_norm = fl / np.median(fl) if np.median(fl) > 0 else fl

    # Simplified spectral templates (key features)
    templates = {
        "SN Ia": {
            "features": [(6150, -0.4, "Si II 6150 absorption"), (3750, -0.3, "Ca II H&K")],
            "description": "Type Ia supernova: Si II absorption at 6150A, no hydrogen",
        },
        "SN II": {
            "features": [(6563, 0.5, "H-alpha emission"), (4861, 0.3, "H-beta"), (4340, 0.2, "H-gamma")],
            "description": "Type II supernova: strong Balmer series emission/P-Cygni",
        },
        "SN Ib/c": {
            "features": [(5876, -0.3, "He I 5876 absorption"), (6678, -0.2, "He I 6678")],
            "description": "Type Ib/c supernova: helium lines (Ib) or neither H nor He (Ic)",
        },
        "TDE": {
            "features": [(4686, 0.5, "He II 4686 emission"), (4640, 0.4, "N III Bowen"), (6563, 0.3, "broad H-alpha")],
            "description": "Tidal disruption event: broad He II + Bowen fluorescence",
        },
        "AGN": {
            "features": [(5007, 0.6, "[OIII] 5007"), (4861, 0.4, "broad H-beta"), (6563, 0.5, "broad H-alpha")],
            "description": "Active galactic nucleus: broad emission lines + [OIII]",
        },
        "Nova": {
            "features": [(6563, 0.7, "very broad H-alpha"), (4861, 0.5, "broad H-beta"), (5007, 0.3, "[OIII]")],
            "description": "Classical nova: very broad Balmer emission + [OIII] development",
        },
    }

    scores = {}
    matched_features = {}

    for cls_name, tmpl in templates.items():
        score = 0.0
        features_found = []

        for center, expected_strength, label in tmpl["features"]:
            # Check if feature present at expected wavelength
            window = 30  # Angstrom
            mask = (wave > center - window) & (wave < center + window)
            if np.sum(mask) < 3:
                continue

            local_flux = fl_norm[mask]
            continuum = np.median(fl_norm[(wave > center - 100) & (wave < center + 100)])

            if continuum <= 0:
                continue

            deviation = (np.mean(local_flux) - continuum) / continuum

            # Positive expected_strength = emission, negative = absorption
            if expected_strength > 0 and deviation > 0.05:
                feature_score = min(deviation / expected_strength, 2.0)
                score += feature_score
                features_found.append({"line": label, "strength": round(float(deviation), 3), "detected": True})
            elif expected_strength < 0 and deviation < -0.05:
                feature_score = min(abs(deviation / expected_strength), 2.0)
                score += feature_score
                features_found.append({"line": label, "strength": round(float(deviation), 3), "detected": True})
            else:
                features_found.append({"line": label, "expected": round(float(expected_strength), 2), "detected": False})

        scores[cls_name] = float(score)
        matched_features[cls_name] = features_found

    if not scores:
        return {"classification": "Unknown", "confidence": 0.0, "error": "No features detected"}

    best_class = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = scores[best_class] / total if total > 0 else 0

    # Rank all classes
    ranked = sorted(scores.items(), key=lambda x: -x[1])

    return {
        "classification": best_class,
        "confidence": round(float(confidence), 3),
        "description": templates[best_class]["description"],
        "scores": {k: round(v, 3) for k, v in ranked},
        "matched_features": matched_features[best_class],
        "all_features": matched_features,
        "method": "spectroscopic_template_matching",
        "redshift_used": z,
    }


def enrich_with_host_galaxy(ra: float, dec: float, search_radius: float = 0.02) -> dict:
    """Query host galaxy information for a transient position."""
    import asyncio

    host_info = {"ra": ra, "dec": dec}

    try:
        from app.connectors.simbad import SIMBADConnector
        connector = SIMBADConnector()

        loop = asyncio.new_event_loop()
        results = loop.run_until_complete(connector.search(
            query="", ra=ra, dec=dec, radius=search_radius
        ))
        loop.close()

        galaxies = [r for r in results if r.object_type and
                    any(t in r.object_type.lower() for t in ["galaxy", "gal", "g"])]

        if galaxies:
            host = galaxies[0]
            host_info["host_name"] = host.name
            host_info["host_type"] = host.object_type
            host_info["host_redshift"] = host.redshift
            # Great-circle (haversine) separation — a flat (ra-host.ra)**2
            # Euclidean distance overstates the RA arm by 1/cos(dec) and
            # mishandles the 0/360 RA wrap, both of which corrupt a displayed
            # provenance number.
            ra1, dec1 = np.radians(ra), np.radians(dec)
            ra2, dec2 = np.radians(host.ra), np.radians(host.dec)
            d_ra = ra2 - ra1
            d_dec = dec2 - dec1
            hav = (
                np.sin(d_dec / 2.0) ** 2
                + np.cos(dec1) * np.cos(dec2) * np.sin(d_ra / 2.0) ** 2
            )
            sep_deg = np.degrees(2.0 * np.arcsin(np.sqrt(hav)))
            host_info["separation_arcsec"] = round(float(sep_deg) * 3600, 2)
            host_info["host_found"] = True
        else:
            host_info["host_found"] = False
            host_info["note"] = "No galaxy found within search radius"
    except Exception as e:
        host_info["host_found"] = False
        host_info["error"] = str(e)

    return host_info
