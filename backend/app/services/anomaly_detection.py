"""Anomaly detection pipeline for astronomical datasets.

Provides three complementary detection methods (Isolation Forest, Autoencoder,
DBSCAN) and an ensemble combiner that flags objects as anomalous only when
at least two methods agree.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.neighbors import NearestNeighbors
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# Columns that should never be used as detection features even if numeric
_EXCLUDED_COLUMNS = {
    "ra",
    "dec",
    "id",
    "object_id",
    "source_id",
    "target_id",
    "index",
    "row_id",
}


# ---------------------------------------------------------------------------
# 1. Isolation Forest
# ---------------------------------------------------------------------------


def isolation_forest_detect(
    data: pd.DataFrame,
    features: list[str],
    contamination: float = 0.05,
) -> pd.DataFrame:
    """Run Isolation Forest anomaly detection.

    Parameters
    ----------
    data : pd.DataFrame
        Input dataset.
    features : list[str]
        Column names to use as input features.
    contamination : float
        Expected fraction of outliers (0, 0.5].

    Returns
    -------
    pd.DataFrame
        Copy of *data* with two added columns:
        - ``if_score``: raw decision_function value (lower = more anomalous).
        - ``if_anomaly``: boolean flag (True for anomalies).
    """
    result = data.copy()
    X = result[features].values.astype(np.float64)

    # Impute NaNs with column medians
    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X)

    clf = IsolationForest(
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X)

    scores = clf.decision_function(X)
    labels = clf.predict(X)

    result["if_score"] = scores
    result["if_anomaly"] = labels == -1

    logger.info(
        "IsolationForest: %d anomalies out of %d samples (contamination=%.3f)",
        int(result["if_anomaly"].sum()),
        len(result),
        contamination,
    )
    return result


# ---------------------------------------------------------------------------
# 2. Autoencoder (MLPRegressor-based)
# ---------------------------------------------------------------------------


def autoencoder_detect(
    data: pd.DataFrame,
    features: list[str],
    threshold_percentile: float = 95,
) -> pd.DataFrame:
    """Run autoencoder-based anomaly detection.

    An :class:`~sklearn.neural_network.MLPRegressor` is trained to reconstruct
    the input (unsupervised).  The per-sample mean squared error serves as the
    anomaly score -- high reconstruction error implies unusual objects.

    Architecture: input_dim -> 64 -> 32 -> 16 -> 32 -> 64 -> input_dim

    Parameters
    ----------
    data : pd.DataFrame
        Input dataset.
    features : list[str]
        Column names to use as input features.
    threshold_percentile : float
        Percentile of reconstruction error above which a sample is flagged.

    Returns
    -------
    pd.DataFrame
        Copy of *data* with two added columns:
        - ``ae_score``: per-sample MSE reconstruction error.
        - ``ae_anomaly``: boolean flag.
    """
    result = data.copy()
    X = result[features].values.astype(np.float64)

    # Impute and scale
    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    input_dim = X_scaled.shape[1]

    # Build symmetric bottleneck architecture
    hidden_layers = (64, 32, 16, 32, 64, input_dim)

    ae = MLPRegressor(
        hidden_layer_sizes=hidden_layers,
        activation="relu",
        solver="adam",
        max_iter=300,
        random_state=42,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=15,
        learning_rate="adaptive",
        learning_rate_init=1e-3,
    )

    # Train: target == input (reconstruction)
    ae.fit(X_scaled, X_scaled)

    X_pred = ae.predict(X_scaled)
    mse = np.mean((X_scaled - X_pred) ** 2, axis=1)

    threshold = np.percentile(mse, threshold_percentile)

    result["ae_score"] = mse
    result["ae_anomaly"] = mse > threshold

    logger.info(
        "Autoencoder: %d anomalies out of %d samples (threshold_pct=%g, threshold=%.4f)",
        int(result["ae_anomaly"].sum()),
        len(result),
        threshold_percentile,
        threshold,
    )
    return result


# ---------------------------------------------------------------------------
# 3. DBSCAN Outlier Detection
# ---------------------------------------------------------------------------


def _estimate_eps_kneedle(distances: np.ndarray) -> float:
    """Estimate DBSCAN eps via the elbow/knee method on sorted k-NN distances.

    We compute the point of maximum curvature in the sorted distance curve
    by finding where the second-order finite difference is maximised.
    """
    sorted_dists = np.sort(distances)
    n = len(sorted_dists)

    if n < 3:
        return float(sorted_dists[-1])

    # Normalise x and y to [0, 1] so curvature comparison is scale-free
    x = np.linspace(0, 1, n)
    y_min, y_max = sorted_dists.min(), sorted_dists.max()
    if y_max - y_min < 1e-12:
        return float(sorted_dists[-1])
    y = (sorted_dists - y_min) / (y_max - y_min)

    # Second-order finite differences (discrete curvature proxy)
    dy = np.gradient(y, x)
    ddy = np.gradient(dy, x)

    # The knee is where the second derivative is maximal
    knee_idx = int(np.argmax(ddy))

    # Map back to original scale
    eps = float(sorted_dists[knee_idx])

    # Safety: eps must be positive
    if eps <= 0:
        eps = float(np.median(sorted_dists))

    return eps


def dbscan_outlier_detect(
    data: pd.DataFrame,
    features: list[str],
    eps: float | str = "auto",
    min_samples: int = 5,
) -> pd.DataFrame:
    """Run DBSCAN-based outlier detection.

    Points assigned ``label == -1`` by DBSCAN are treated as outliers.

    Parameters
    ----------
    data : pd.DataFrame
        Input dataset.
    features : list[str]
        Column names to use as input features.
    eps : float or ``'auto'``
        Neighbourhood radius.  If ``'auto'``, estimate from k-NN distances
        using the elbow method.
    min_samples : int
        Minimum points to form a dense region.

    Returns
    -------
    pd.DataFrame
        Copy of *data* with two added columns:
        - ``dbscan_label``: cluster label (-1 = noise/outlier).
        - ``dbscan_anomaly``: boolean flag.
    """
    result = data.copy()
    X = result[features].values.astype(np.float64)

    # Impute and scale
    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Estimate eps automatically if requested
    if eps == "auto":
        k = min(min_samples, len(X_scaled) - 1)
        if k < 1:
            k = 1
        nn = NearestNeighbors(n_neighbors=k, n_jobs=-1)
        nn.fit(X_scaled)
        distances, _ = nn.kneighbors(X_scaled)
        # Take the distance to the k-th nearest neighbour for each point
        kth_distances = distances[:, -1]
        eps = _estimate_eps_kneedle(kth_distances)
        logger.info("DBSCAN auto-eps estimated: %.4f", eps)

    db = DBSCAN(eps=eps, min_samples=min_samples, n_jobs=-1)
    labels = db.fit_predict(X_scaled)

    result["dbscan_label"] = labels
    result["dbscan_anomaly"] = labels == -1

    n_clusters = len(set(labels) - {-1})
    logger.info(
        "DBSCAN: %d outliers, %d clusters out of %d samples (eps=%.4f, min_samples=%d)",
        int(result["dbscan_anomaly"].sum()),
        n_clusters,
        len(result),
        eps,
        min_samples,
    )
    return result


# ---------------------------------------------------------------------------
# 4. Ensemble: detect_anomalies
# ---------------------------------------------------------------------------


def _select_numeric_features(data: pd.DataFrame) -> list[str]:
    """Select numeric columns suitable for anomaly detection."""
    numeric_cols = data.select_dtypes(include=[np.number]).columns.tolist()
    features = [
        c
        for c in numeric_cols
        if c.lower() not in _EXCLUDED_COLUMNS
        and not c.lower().endswith("_id")
        and not c.lower().startswith("id_")
    ]
    return features


def _feature_importances_from_if(
    data: pd.DataFrame,
    features: list[str],
    anomaly_mask: np.ndarray,
    top_n: int = 5,
) -> list[str]:
    """Rank features by how much anomalies deviate from normal objects.

    Uses the absolute z-score difference between anomalous and normal
    populations for each feature.
    """
    if anomaly_mask.sum() == 0 or (~anomaly_mask).sum() == 0:
        return features[:top_n]

    importance = {}
    for feat in features:
        col = pd.to_numeric(data[feat], errors="coerce")
        normal_vals = col[~anomaly_mask].dropna()
        anomaly_vals = col[anomaly_mask].dropna()
        if len(normal_vals) < 2 or len(anomaly_vals) == 0:
            importance[feat] = 0.0
            continue
        mu = normal_vals.mean()
        sigma = normal_vals.std()
        if sigma < 1e-12:
            importance[feat] = 0.0
            continue
        # Mean absolute z-score of anomalies relative to normal distribution
        importance[feat] = float(np.mean(np.abs((anomaly_vals - mu) / sigma)))

    ranked = sorted(importance, key=importance.get, reverse=True)
    return ranked[:top_n]


def detect_anomalies(
    data: pd.DataFrame,
    features: Optional[list[str]] = None,
    methods: str | list[str] = "all",
    if_contamination: float = 0.05,
    ae_threshold_percentile: float = 95,
    dbscan_min_samples: int = 5,
    voting_threshold: int = 2,
) -> pd.DataFrame:
    """Run ensemble anomaly detection and combine results.

    Parameters
    ----------
    data : pd.DataFrame
        Input dataset.
    features : list[str] or None
        Columns to analyse.  ``None`` = auto-select all suitable numeric cols.
    methods : str or list[str]
        ``'all'`` to run every method, or a list like
        ``['isolation_forest', 'autoencoder', 'dbscan']``.
    if_contamination : float
        Contamination parameter for Isolation Forest (0, 0.5].
    ae_threshold_percentile : float
        Percentile threshold for the autoencoder reconstruction error.
    dbscan_min_samples : int
        Minimum samples for DBSCAN dense region formation.
    voting_threshold : int
        Minimum number of methods that must flag a sample for ensemble agreement.

    Returns
    -------
    pd.DataFrame
        Original data augmented with per-method columns plus:
        - ``anomaly_score``: normalised [0, 1] ensemble score.
        - ``is_anomaly``: True if flagged by >= 2 methods.
        - ``anomaly_methods``: list of method names that flagged the row.
        - ``anomaly_features``: most important features driving the anomaly.
        Rows are sorted with anomalies first (descending score).
    """
    if len(data) < 30:
        logger.warning(
            "Fewer than 30 rows (%d) -- skipping anomaly detection.", len(data)
        )
        result = data.copy()
        result["is_anomaly"] = False
        result["anomaly_score"] = 0.0
        result["anomaly_methods"] = [[] for _ in range(len(result))]
        result["anomaly_features"] = [[] for _ in range(len(result))]
        return result

    if features is None:
        features = _select_numeric_features(data)
        logger.info("Auto-selected %d features: %s", len(features), features)

    if len(features) == 0:
        logger.warning("No numeric features found -- skipping anomaly detection.")
        result = data.copy()
        result["is_anomaly"] = False
        result["anomaly_score"] = 0.0
        result["anomaly_methods"] = [[] for _ in range(len(result))]
        result["anomaly_features"] = [[] for _ in range(len(result))]
        return result

    # Determine which methods to run
    if methods == "all":
        run_methods = ["isolation_forest", "autoencoder", "dbscan"]
    elif isinstance(methods, str):
        run_methods = [methods]
    else:
        run_methods = list(methods)

    result = data.copy()

    # ── Run individual detectors ──────────────────────────────────────────

    if "isolation_forest" in run_methods:
        try:
            result = isolation_forest_detect(result, features, contamination=if_contamination)
        except Exception:
            logger.exception("Isolation Forest failed")
            result["if_anomaly"] = False
            result["if_score"] = 0.0

    if "autoencoder" in run_methods:
        try:
            result = autoencoder_detect(result, features, threshold_percentile=ae_threshold_percentile)
        except Exception:
            logger.exception("Autoencoder failed")
            result["ae_anomaly"] = False
            result["ae_score"] = 0.0

    if "dbscan" in run_methods:
        try:
            result = dbscan_outlier_detect(result, features, min_samples=dbscan_min_samples)
        except Exception:
            logger.exception("DBSCAN failed")
            result["dbscan_anomaly"] = False
            result["dbscan_label"] = 0

    # ── Combine results ───────────────────────────────────────────────────

    method_flags = {
        "isolation_forest": "if_anomaly",
        "autoencoder": "ae_anomaly",
        "dbscan": "dbscan_anomaly",
    }

    # Count how many methods flagged each row
    votes = pd.DataFrame(index=result.index)
    active_methods = []
    for method_name, col in method_flags.items():
        if col in result.columns:
            votes[method_name] = result[col].astype(int)
            active_methods.append(method_name)

    if len(active_methods) == 0:
        result["is_anomaly"] = False
        result["anomaly_score"] = 0.0
        result["anomaly_methods"] = [[] for _ in range(len(result))]
        result["anomaly_features"] = [[] for _ in range(len(result))]
        return result

    vote_count = votes[active_methods].sum(axis=1)

    # Ensemble anomaly: flagged by >= voting_threshold methods (or >= 1 if only 1 method ran)
    effective_threshold = voting_threshold if len(active_methods) >= voting_threshold else 1
    result["is_anomaly"] = vote_count >= effective_threshold

    # Normalised ensemble score in [0, 1]
    # Blend: proportion of methods that flagged + normalised per-method scores
    score_components = []
    score_cols = {
        "isolation_forest": "if_score",
        "autoencoder": "ae_score",
    }
    for method_name, col in score_cols.items():
        if col in result.columns:
            raw = result[col].values.astype(np.float64)
            # For IF, lower score = more anomalous, so invert
            if method_name == "isolation_forest":
                raw = -raw
            rmin, rmax = np.nanmin(raw), np.nanmax(raw)
            if rmax - rmin > 1e-12:
                normalised = (raw - rmin) / (rmax - rmin)
            else:
                normalised = np.zeros_like(raw)
            score_components.append(normalised)

    # Vote fraction as an additional component
    vote_frac = vote_count.values / len(active_methods)
    score_components.append(vote_frac)

    ensemble_score = np.mean(score_components, axis=0)
    result["anomaly_score"] = ensemble_score

    # Per-row list of methods that flagged it
    anomaly_methods_list = []
    for idx in result.index:
        flagged = [m for m in active_methods if votes.loc[idx, m] == 1]
        anomaly_methods_list.append(flagged)
    result["anomaly_methods"] = anomaly_methods_list

    # Feature importance (from IF deviation analysis)
    anomaly_mask = result["is_anomaly"].values.astype(bool)
    top_features = _feature_importances_from_if(result, features, anomaly_mask)
    # Assign to all anomalous rows; empty for normal rows
    result["anomaly_features"] = [
        top_features if is_anom else [] for is_anom in anomaly_mask
    ]

    # Sort anomalies to the top
    result = result.sort_values(
        ["is_anomaly", "anomaly_score"], ascending=[False, False]
    ).reset_index(drop=True)

    n_anomalies = int(result["is_anomaly"].sum())
    logger.info(
        "Ensemble: %d anomalies out of %d samples (%d methods active)",
        n_anomalies,
        len(result),
        len(active_methods),
    )

    return result
