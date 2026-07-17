"""Pinned registry and fail-closed reader for official DESI DR2 chains.

The DESI release stores *analysis combinations* (model + BAO + CMB + SN),
not standalone likelihood data products.  They therefore live outside the
``CosmologyDatasetEntry`` registry: treating a published posterior chain as a
likelihood would double-count its priors and nuisance marginalisation.

Only files listed in the official DESI v1.0 SHA-256 manifest are accepted.
The runtime never downloads chains implicitly.  Operators may mirror the
official directory and point ``DESI_DR2_OFFICIAL_CHAIN_ROOT`` at its root; a
missing, incomplete or modified mirror returns a WITHHELD record with no
parameter intervals.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml


AnalysisEvidenceTier = Literal[
    "published_external",
    "platform_exact",
    "platform_compressed",
    "withheld",
]

DESI_DR2_ANALYSIS_REGISTRY_VERSION = "2026-07-17"
DESI_DR2_CHAIN_RELEASE_VERSION = "DR2 BAO cosmology chains v1.0"
DESI_DR2_CHAIN_ROOT_URL = (
    "https://data.desi.lbl.gov/public/papers/y3/bao-cosmo-params"
)
DESI_DR2_CHAIN_LANDING_URL = DESI_DR2_CHAIN_ROOT_URL + "/README.html"
DESI_DR2_CHAIN_MANIFEST_URL = (
    DESI_DR2_CHAIN_ROOT_URL
    + "/dr2_vac_dr2_bao-cosmo-params_v1.0.sha256sum"
)
# Digest observed from the complete official manifest on 2026-07-17.  Each
# registered artifact below is also pinned by its own manifest-provided hash.
DESI_DR2_CHAIN_MANIFEST_SHA256 = (
    "df78872aa8b2d3473a9e8de78f498180efd7cbcbeb18211ce4787fac52067ee5"
)
DESI_DATA_LICENSE_URL = "https://data.desi.lbl.gov/doc/acknowledgments/"
DESI_DATA_LICENSE = "CC-BY-4.0"
DESI_DR2_CHAIN_CACHE_ENV = "DESI_DR2_OFFICIAL_CHAIN_ROOT"

_OFFICIAL_CMB_COMPONENTS = (
    "planck2018-lowl-TT-clik",
    "planck2018-lowl-EE-clik",
    "planck-NPIPE-highl-CamSpec-TTTEEE",
    "planck-act-dr6-lensing",
)
_OFFICIAL_CMB_FOLDER = "_".join(_OFFICIAL_CMB_COMPONENTS)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RMINUS1_LIMIT = 0.01
_MIN_KISH_WEIGHT_ESS = 1000.0
_REFERENCE_CENTER_MAX_SIGMA = 0.1
_REFERENCE_WIDTH_MAX_FRACTION = 0.05
_CONTOUR_PAIRS = (("w0", "wa"), ("omegam", "H0"))
_CONTOUR_GRID_BINS = 32
_NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_MARGESTATS_68_RE = re.compile(
    rf"^\s*(?P<parameter>\S+)\s+"
    rf"(?P<center>{_NUMBER_PATTERN})"
    rf"(?:\\pm\s*(?P<symmetric>{_NUMBER_PATTERN})"
    rf"|\^\{{\+(?P<upper>{_NUMBER_PATTERN})\}}_\{{-(?P<lower>{_NUMBER_PATTERN})\}})"
)


@dataclass(frozen=True)
class OfficialChainArtifact:
    """One byte-pinned file from an official analysis directory."""

    role: Literal["chain", "config", "checkpoint", "reference_summary"]
    relative_path: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{DESI_DR2_CHAIN_ROOT_URL}/{self.relative_path}"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "url": self.url}


@dataclass(frozen=True)
class CosmologyAnalysisEntry:
    """A published posterior analysis, not a reusable likelihood factor."""

    key: str
    display_name: str
    release: str
    model: Literal["wcdm", "w0wa_cdm"]
    supernova_selection: Literal["pantheon_plus", "union3", "des_sn5yr"]
    official_model_folder: Literal["base_w", "base_w_wa"]
    official_sn_component: Literal["pantheonplus", "union3", "desy5sn"]
    data_components: tuple[str, ...]
    parameter_map: tuple[tuple[str, str], ...]
    weight_column: str
    burn_in_rule: str
    chain_format: str
    evidence_tier: AnalysisEvidenceTier
    source_url: str
    paper_arxiv: str
    paper_doi: str
    license_name: str
    license_url: str
    overlap_groups: tuple[str, ...]
    artifacts: tuple[OfficialChainArtifact, ...]
    notes: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["parameter_map"] = dict(self.parameter_map)
        result["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        result["manifest_url"] = DESI_DR2_CHAIN_MANIFEST_URL
        result["manifest_sha256"] = DESI_DR2_CHAIN_MANIFEST_SHA256
        return result


class OfficialChainValidationError(ValueError):
    """A mirrored official analysis failed a scientific integrity check."""


# Hashes below come verbatim from the official DESI v1.0 SHA-256 manifest.
# Do not replace a value from a mutable directory listing or a remembered
# paper table.  A new upstream release gets a new registry version/entry.
_ARTIFACT_HASHES: dict[tuple[str, str], dict[str, str]] = {
    ("w0wa_cdm", "pantheon_plus"): {
        "chain.updated.yaml": "ce490ac81bd61fa3ca65738666dde4a1f3d07c64983fdc0d2646cdab1a9a14b9",
        "chain.checkpoint": "cb933f87d512f850a095d124fb8dd20237e743479cb37679790a45c0996e1716",
        "chain.1.txt": "db81d299d59051ae5e1e8f67952320e08a1644a4a1f8d19267705aee32798877",
        "chain.2.txt": "442390266f6a3bfe88e9c7cd8e29d1e7cff47fc8582f3a3af80a6b40b9f83939",
        "chain.3.txt": "1c92edf523df34784633f6852f7564f3d953203ae939d5d2e8cd4c3fd6f580ae",
        "chain.4.txt": "b17dc02689c3a30dd34a96d91ac35180006f17d10b82331396ef55ce999594af",
        "chain.margestats": "03b712b18991c4b125851d3d924936fca03f6047ac4b68fb888067e9ef05f3c1",
    },
    ("w0wa_cdm", "union3"): {
        "chain.updated.yaml": "2fc4c5ccca64f718992409b28e54f367603056073b9ca3d2781ed0e18a9ec3cd",
        "chain.checkpoint": "fa4a3634368730592370908635265ad3aba753c5bf8c02e61fe7d34af83fab53",
        "chain.1.txt": "f95d091203f615e1654ea9f4fea332db08fa66c59a5fe188b4915e67e1d73b11",
        "chain.2.txt": "23aac58326257d207b8ee3699d21ed908e46238e42041d8c2ccf12fd85c8b839",
        "chain.3.txt": "9e0998c5685e7552b94fd45e985dd137604f34692bebbc6fb922a1588669ab6a",
        "chain.4.txt": "7710ababc2ab0c7fb5f3f67c8b790409703bce18707a2f99a1cd3fea531b4c20",
        "chain.margestats": "60f7a661abb62b4b3033c6ec70efec8b2a36e94ed4bbf2ce262c7e6af168b888",
    },
    ("w0wa_cdm", "des_sn5yr"): {
        "chain.updated.yaml": "b6af043a9557c66ac94a984f322984d7720e6e0707702435ecc471d6ca28fef8",
        "chain.checkpoint": "694a95d8e730390201aa1b4654b1f71b918cccd255e24dbdcbc379fb23322cc0",
        "chain.1.txt": "8c783ebf283a205b7f569ce36a6694a32646ced5e28fd3b4683742733f6165e0",
        "chain.2.txt": "cd4f2ff3a66aa92aceecd47c8452520c2de09d887f231fc0df033cd68597a886",
        "chain.3.txt": "f7f8dbf28ff23d371e0b987b938d321adb920f1a09e97f746b3c0bb2d6247a01",
        "chain.4.txt": "4a3607867d34890832f431c4d61947e5572a72ff1a407141f4b8cb8d3d7ec769",
        "chain.margestats": "3fc6e841b7fd9cb21bac340636f1ba23bb96c75fa8b3b292fa5fbb4426bc8d8a",
    },
}

_MODEL_FOLDERS = {"wcdm": "base_w", "w0wa_cdm": "base_w_wa"}
_SN_COMPONENTS = {
    "pantheon_plus": "pantheonplus",
    "union3": "union3",
    "des_sn5yr": "desy5sn",
}


def _artifacts_for(model: str, sn_selection: str) -> tuple[OfficialChainArtifact, ...]:
    model_folder = _MODEL_FOLDERS[model]
    sn_component = _SN_COMPONENTS[sn_selection]
    analysis_folder = (
        f"desi-bao-all_{sn_component}_{_OFFICIAL_CMB_FOLDER}"
    )
    prefix = f"cobaya/{model_folder}/{analysis_folder}"
    hashes = _ARTIFACT_HASHES[(model, sn_selection)]
    artifacts: list[OfficialChainArtifact] = []
    for filename, digest in hashes.items():
        if filename.endswith(".txt"):
            role: Literal[
                "chain", "config", "checkpoint", "reference_summary"
            ] = "chain"
        elif filename.endswith(".yaml"):
            role = "config"
        elif filename.endswith(".margestats"):
            role = "reference_summary"
        else:
            role = "checkpoint"
        artifacts.append(
            OfficialChainArtifact(
                role=role,
                relative_path=f"{prefix}/{filename}",
                sha256=digest,
            )
        )
    return tuple(artifacts)


def _make_entry(model: str, sn_selection: str) -> CosmologyAnalysisEntry:
    model_folder = _MODEL_FOLDERS[model]
    sn_component = _SN_COMPONENTS[sn_selection]
    folder = f"desi-bao-all_{sn_component}_{_OFFICIAL_CMB_FOLDER}"
    parameter_map = (
        (("w", "w"), ("H0", "H0"), ("omegam", "omegam"))
        if model == "wcdm"
        else (("w0", "w"), ("wa", "wa"), ("H0", "H0"), ("omegam", "omegam"))
    )
    sn_note = (
        "The official 'pantheonplus' component is the uncalibrated Pantheon+ "
        "SN likelihood. It is not the platform's SH0ES-calibrated "
        "'pantheon_plus' dataset entry."
        if sn_selection == "pantheon_plus"
        else "The SN component name follows the official DESI release."
    )
    return CosmologyAnalysisEntry(
        key=f"desi_dr2_{model}_{sn_selection}_default_cmb",
        display_name=f"DESI DR2 {model} + {sn_component} + default CMB",
        release=DESI_DR2_CHAIN_RELEASE_VERSION,
        model=model,  # type: ignore[arg-type]
        supernova_selection=sn_selection,  # type: ignore[arg-type]
        official_model_folder=model_folder,  # type: ignore[arg-type]
        official_sn_component=sn_component,  # type: ignore[arg-type]
        data_components=("desi-bao-all", sn_component, *_OFFICIAL_CMB_COMPONENTS),
        parameter_map=parameter_map,
        weight_column="weight",
        burn_in_rule="released Cobaya chains; checkpoint burn_in must equal 0",
        chain_format="Cobaya ASCII: header, weight, minuslogpost, parameters",
        evidence_tier="published_external",
        source_url=f"{DESI_DR2_CHAIN_ROOT_URL}/cobaya/{model_folder}/{folder}/",
        paper_arxiv="2503.14738",
        paper_doi="10.1103/tr6y-kpc6",
        license_name=DESI_DATA_LICENSE,
        license_url=DESI_DATA_LICENSE_URL,
        overlap_groups=(
            "desi_dr2_bao:all",
            "cmb:planck_pr3_pr4_act_dr6",
            f"sn:{sn_component}",
        ),
        artifacts=_artifacts_for(model, sn_selection),
        notes=(
            "Published posterior samples are context/evidence, not a likelihood "
            "factor. " + sn_note
        ),
    )


_ANALYSIS_REGISTRY: dict[str, CosmologyAnalysisEntry] = {
    entry.key: entry
    for entry in (
        _make_entry(model, sn)
        # The first safe release registers only the three headline base_w_wa
        # combinations, for which the official release includes complete
        # four-chain and checkpoint products. Other accepted
        # model inputs fail closed until their exact products receive the same
        # independent contract audit; no nearest-model substitution occurs.
        for model in ("w0wa_cdm",)
        for sn in ("pantheon_plus", "union3", "des_sn5yr")
    )
}


def list_cosmology_analyses(
    *, model: str | None = None, supernova_selection: str | None = None
) -> list[CosmologyAnalysisEntry]:
    entries = sorted(_ANALYSIS_REGISTRY.values(), key=lambda entry: entry.key)
    if model is not None:
        entries = [entry for entry in entries if entry.model == model]
    if supernova_selection is not None:
        entries = [
            entry
            for entry in entries
            if entry.supernova_selection == supernova_selection
        ]
    return entries


def get_cosmology_analysis(
    *, model: str, supernova_selection: str
) -> CosmologyAnalysisEntry | None:
    key = f"desi_dr2_{model}_{supernova_selection}_default_cmb"
    return _ANALYSIS_REGISTRY.get(key)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_artifact(root: Path, artifact: OfficialChainArtifact) -> Path:
    root = root.expanduser().resolve()
    candidate = (root / artifact.relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise OfficialChainValidationError(
            f"artifact_path_escapes_cache_root:{artifact.relative_path}"
        ) from exc
    return candidate


def _verify_artifacts(
    root: Path, entry: CosmologyAnalysisEntry
) -> dict[str, Path]:
    verified: dict[str, Path] = {}
    for artifact in entry.artifacts:
        path = _resolve_artifact(root, artifact)
        if not path.is_file():
            raise OfficialChainValidationError(
                f"official_artifact_missing:{artifact.relative_path}"
            )
        digest = _file_sha256(path)
        if digest != artifact.sha256:
            raise OfficialChainValidationError(
                f"official_artifact_checksum_mismatch:{artifact.relative_path}"
            )
        verified[artifact.relative_path] = path
    return verified


def _read_checkpoint(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        record = payload["sampler"]["mcmc"]
        converged = record.get("converged") is True
        rminus1 = float(record["Rminus1_last"])
        burn_in = int(record["burn_in"])
        mpi_size = int(record["mpi_size"])
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as exc:
        raise OfficialChainValidationError("invalid_official_checkpoint") from exc
    if not converged:
        raise OfficialChainValidationError("official_checkpoint_not_converged")
    if not math.isfinite(rminus1) or rminus1 >= _RMINUS1_LIMIT:
        raise OfficialChainValidationError("official_checkpoint_rminus1_failed")
    if burn_in != 0:
        raise OfficialChainValidationError("unsupported_official_burn_in_rule")
    if mpi_size != 4:
        raise OfficialChainValidationError("unexpected_official_chain_count")
    return {
        "converged": True,
        "rminus1_last": rminus1,
        "burn_in": burn_in,
        "mpi_size": mpi_size,
    }


def _read_chain_columns(
    path: Path,
    *,
    parameter_map: dict[str, str],
    weight_column: str,
) -> tuple[list[float], dict[str, list[float]], int, tuple[str, ...]]:
    weights: list[float] = []
    values: dict[str, list[float]] = {name: [] for name in parameter_map}
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline().strip()
        if not header.startswith("#"):
            raise OfficialChainValidationError(
                f"official_chain_header_missing:{path.name}"
            )
        columns = header[1:].split()
        if len(columns) != len(set(columns)):
            raise OfficialChainValidationError(
                f"official_chain_duplicate_columns:{path.name}"
            )
        required = {weight_column, "minuslogpost", *parameter_map.values()}
        missing = sorted(required - set(columns))
        if missing:
            raise OfficialChainValidationError(
                "official_chain_missing_columns:" + ",".join(missing)
            )
        indices = {name: columns.index(name) for name in required}
        for line_number, raw in enumerate(handle, start=2):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) != len(columns):
                raise OfficialChainValidationError(
                    f"official_chain_row_width:{path.name}:{line_number}"
                )
            try:
                weight = float(fields[indices[weight_column]])
                row_values = {
                    platform_name: float(fields[indices[official_name]])
                    for platform_name, official_name in parameter_map.items()
                }
            except (ValueError, OverflowError) as exc:
                raise OfficialChainValidationError(
                    f"official_chain_non_numeric:{path.name}:{line_number}"
                ) from exc
            if not math.isfinite(weight) or weight <= 0:
                raise OfficialChainValidationError(
                    f"official_chain_invalid_weight:{path.name}:{line_number}"
                )
            if any(not math.isfinite(value) for value in row_values.values()):
                raise OfficialChainValidationError(
                    f"official_chain_non_finite_parameter:{path.name}:{line_number}"
                )
            weights.append(weight)
            for name, value in row_values.items():
                values[name].append(value)
    if not weights:
        raise OfficialChainValidationError(f"official_chain_empty:{path.name}")
    return weights, values, len(weights), tuple(columns)


def _weighted_quantile(
    values: np.ndarray, weights: np.ndarray, quantile: float
) -> float:
    order = np.argsort(values, kind="mergesort")
    ordered_values = values[order]
    ordered_weights = weights[order]
    positions = np.cumsum(ordered_weights) - 0.5 * ordered_weights
    positions /= float(np.sum(ordered_weights))
    return float(np.interp(quantile, positions, ordered_values))


def _read_reference_margestats(path: Path) -> dict[str, dict[str, float]]:
    """Parse the byte-pinned GetDist 68% summary shipped by DESI."""

    parsed: dict[str, dict[str, float]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OfficialChainValidationError(
            "official_reference_summary_unreadable"
        ) from exc
    for line in lines:
        match = _MARGESTATS_68_RE.match(line)
        if match is None:
            continue
        center = float(match.group("center"))
        symmetric = match.group("symmetric")
        if symmetric is not None:
            lower_error = upper_error = abs(float(symmetric))
        else:
            lower_error = abs(float(match.group("lower")))
            upper_error = abs(float(match.group("upper")))
        width = lower_error + upper_error
        if not all(
            math.isfinite(value) and value > 0
            for value in (lower_error, upper_error, width)
        ) or not math.isfinite(center):
            raise OfficialChainValidationError(
                f"official_reference_summary_invalid:{match.group('parameter')}"
            )
        parsed[match.group("parameter")] = {
            "center": center,
            "lower_error": lower_error,
            "upper_error": upper_error,
            "interval_width": width,
        }
    if not parsed:
        raise OfficialChainValidationError("official_reference_summary_empty")
    return parsed


def _validate_reference_acceptance(
    summaries: dict[str, dict[str, float]],
    *,
    entry: CosmologyAnalysisEntry,
    reference_path: Path,
) -> dict[str, Any]:
    """Require recomputed intervals to reproduce DESI's pinned GetDist output."""

    official = _read_reference_margestats(reference_path)
    checks: dict[str, dict[str, Any]] = {}
    for platform_name, official_name in entry.parameter_map:
        if official_name not in official or platform_name not in summaries:
            raise OfficialChainValidationError(
                f"official_reference_parameter_missing:{platform_name}"
            )
        reference = official[official_name]
        computed = summaries[platform_name]
        computed_width = float(computed["q84"]) - float(computed["q16"])
        reference_width = float(reference["interval_width"])
        reference_sigma = reference_width / 2.0
        center_delta_sigma = (
            abs(float(computed["mean"]) - float(reference["center"]))
            / reference_sigma
        )
        width_delta_fraction = abs(computed_width - reference_width) / reference_width
        center_pass = center_delta_sigma <= _REFERENCE_CENTER_MAX_SIGMA + 1e-12
        width_pass = width_delta_fraction <= _REFERENCE_WIDTH_MAX_FRACTION + 1e-12
        checks[platform_name] = {
            "official_parameter": official_name,
            "computed_center": float(computed["mean"]),
            "reference_center": float(reference["center"]),
            "center_delta_sigma": center_delta_sigma,
            "computed_interval_width": computed_width,
            "reference_interval_width": reference_width,
            "width_delta_fraction": width_delta_fraction,
            "center_pass": center_pass,
            "width_pass": width_pass,
        }
        if not center_pass:
            raise OfficialChainValidationError(
                f"official_reference_center_mismatch:{platform_name}"
            )
        if not width_pass:
            raise OfficialChainValidationError(
                f"official_reference_width_mismatch:{platform_name}"
            )
    return {
        "status": "PASSED",
        "reference_artifact": str(reference_path.name),
        "center_max_sigma": _REFERENCE_CENTER_MAX_SIGMA,
        "interval_width_max_fraction": _REFERENCE_WIDTH_MAX_FRACTION,
        "checks": checks,
    }


def _weighted_2d_contour_grid(
    x_values: np.ndarray,
    y_values: np.ndarray,
    weights: np.ndarray,
    *,
    x_parameter: str,
    y_parameter: str,
) -> dict[str, Any] | None:
    x_low = _weighted_quantile(x_values, weights, 0.005)
    x_high = _weighted_quantile(x_values, weights, 0.995)
    y_low = _weighted_quantile(y_values, weights, 0.005)
    y_high = _weighted_quantile(y_values, weights, 0.995)
    if not x_high > x_low or not y_high > y_low:
        return None
    histogram, x_edges, y_edges = np.histogram2d(
        x_values,
        y_values,
        bins=_CONTOUR_GRID_BINS,
        range=((x_low, x_high), (y_low, y_high)),
        weights=weights,
    )
    captured_weight = float(np.sum(histogram))
    total_weight = float(np.sum(weights))
    if not math.isfinite(captured_weight) or captured_weight <= 0:
        return None
    probability = histogram / captured_weight
    ordered = np.sort(probability.ravel())[::-1]
    cumulative = np.cumsum(ordered)
    credible_regions: dict[str, dict[str, float]] = {}
    for level in (0.68, 0.95):
        index = min(int(np.searchsorted(cumulative, level)), len(ordered) - 1)
        threshold = float(ordered[index])
        enclosed = float(np.sum(probability[probability >= threshold]))
        credible_regions[f"{level:.2f}"] = {
            "minimum_bin_probability": threshold,
            "enclosed_grid_probability": enclosed,
        }
    return {
        "x_parameter": x_parameter,
        "y_parameter": y_parameter,
        "representation": "weighted_histogram2d_hpd_display",
        "grid_bins_per_axis": _CONTOUR_GRID_BINS,
        "range_weighted_quantiles": [0.005, 0.995],
        "captured_weight_fraction": captured_weight / total_weight,
        "x_edges": np.round(x_edges, 12).tolist(),
        "y_edges": np.round(y_edges, 12).tolist(),
        # Axis order is explicit so plotting clients cannot accidentally
        # transpose w0/wa and report a different degeneracy direction.
        "probability_grid_axis_order": ["x_bin", "y_bin"],
        "probability_grid": np.round(probability, 12).tolist(),
        "credible_regions": credible_regions,
        "interpretation": (
            "Empirical display grid for visual overlap only; not a Gaussian "
            "tension significance or a cross-covariance estimate."
        ),
    }


def _summaries_from_chains(
    chain_paths: list[Path], entry: CosmologyAnalysisEntry
) -> tuple[
    dict[str, dict[str, float]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    parameter_map = dict(entry.parameter_map)
    all_weights: list[float] = []
    all_values: dict[str, list[float]] = {name: [] for name in parameter_map}
    rows_per_chain: list[int] = []
    expected_header: tuple[str, ...] | None = None
    for path in chain_paths:
        weights, values, row_count, header = _read_chain_columns(
            path,
            parameter_map=parameter_map,
            weight_column=entry.weight_column,
        )
        if expected_header is None:
            expected_header = header
        elif header != expected_header:
            raise OfficialChainValidationError(
                f"official_chain_header_mismatch:{path.name}"
            )
        all_weights.extend(weights)
        rows_per_chain.append(row_count)
        for name in parameter_map:
            all_values[name].extend(values[name])

    weight_array = np.asarray(all_weights, dtype=float)
    sum_weights = float(np.sum(weight_array))
    sum_weight_squares = float(np.sum(weight_array ** 2))
    kish_ess = sum_weights ** 2 / sum_weight_squares
    if not math.isfinite(kish_ess) or kish_ess < _MIN_KISH_WEIGHT_ESS:
        raise OfficialChainValidationError("official_chain_weight_ess_below_1000")

    summaries: dict[str, dict[str, float]] = {}
    value_arrays: dict[str, np.ndarray] = {}
    for name, raw_values in all_values.items():
        value_array = np.asarray(raw_values, dtype=float)
        value_arrays[name] = value_array
        mean = float(np.average(value_array, weights=weight_array))
        variance = float(np.average((value_array - mean) ** 2, weights=weight_array))
        summaries[name] = {
            "mean": mean,
            "std": math.sqrt(max(variance, 0.0)),
            "q16": _weighted_quantile(value_array, weight_array, 0.16),
            "median": _weighted_quantile(value_array, weight_array, 0.50),
            "q84": _weighted_quantile(value_array, weight_array, 0.84),
        }
    contours: dict[str, dict[str, Any]] = {}
    for x_parameter, y_parameter in _CONTOUR_PAIRS:
        if x_parameter not in value_arrays or y_parameter not in value_arrays:
            continue
        contour = _weighted_2d_contour_grid(
            value_arrays[x_parameter],
            value_arrays[y_parameter],
            weight_array,
            x_parameter=x_parameter,
            y_parameter=y_parameter,
        )
        if contour is not None:
            contours[f"{x_parameter}__{y_parameter}"] = contour
    return summaries, contours, {
        "chain_parts": len(chain_paths),
        "rows_per_chain": rows_per_chain,
        "rows_total": sum(rows_per_chain),
        "column_order": list(expected_header or ()),
        "sum_weights": sum_weights,
        "kish_weight_ess": kish_ess,
        "kish_weight_ess_note": (
            "Weight-degeneracy check only; the official Cobaya checkpoint "
            "provides the between-chain convergence diagnostic."
        ),
    }


def summarize_official_analysis(
    entry: CosmologyAnalysisEntry,
    *,
    cache_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify and summarize a locally mirrored official DESI analysis.

    The result is intentionally non-publication-ready at this aggregate layer.
    It may expose verified intervals as ``published_external`` context, but it
    never turns a published posterior into a reusable likelihood.
    """

    base: dict[str, Any] = {
        "analysis_id": entry.key,
        "analysis": entry.to_dict(),
        "publication_ready": False,
        "claim_scope": "published_external_chain_context",
        "evidence_tier": entry.evidence_tier,
        "support_artifacts": [artifact.to_dict() for artifact in entry.artifacts],
    }
    configured = cache_root
    if configured is None:
        configured = os.getenv(DESI_DR2_CHAIN_CACHE_ENV)
    if not str(configured or "").strip():
        return {
            **base,
            "success": False,
            "status": "WITHHELD",
            "parameter_intervals": {},
            "withheld_reasons": ["official_chain_cache_not_configured"],
        }

    root = Path(str(configured)).expanduser()
    try:
        verified = _verify_artifacts(root, entry)
        checkpoint_artifact = next(
            artifact for artifact in entry.artifacts if artifact.role == "checkpoint"
        )
        checkpoint = _read_checkpoint(verified[checkpoint_artifact.relative_path])
        chain_artifacts = sorted(
            (artifact for artifact in entry.artifacts if artifact.role == "chain"),
            key=lambda artifact: artifact.relative_path,
        )
        if len(chain_artifacts) != 4:
            raise OfficialChainValidationError("official_chain_requires_four_parts")
        summaries, contours, diagnostics = _summaries_from_chains(
            [verified[artifact.relative_path] for artifact in chain_artifacts],
            entry,
        )
        reference_artifact = next(
            artifact
            for artifact in entry.artifacts
            if artifact.role == "reference_summary"
        )
        reference_acceptance = _validate_reference_acceptance(
            summaries,
            entry=entry,
            reference_path=verified[reference_artifact.relative_path],
        )
    except (OfficialChainValidationError, OSError, StopIteration) as exc:
        reason = str(exc) or exc.__class__.__name__
        return {
            **base,
            "success": False,
            "status": "WITHHELD",
            "parameter_intervals": {},
            "withheld_reasons": [reason],
        }

    return {
        **base,
        "success": True,
        "status": "READY",
        "parameter_intervals": summaries,
        "two_dimensional_contours": contours,
        "diagnostics": {
            **diagnostics,
            "checkpoint": checkpoint,
            "official_reference_acceptance": reference_acceptance,
        },
        "withheld_reasons": [],
    }


def audit_cosmology_analysis_registry() -> list[str]:
    """Static integrity audit for the official combination registry."""

    issues: list[str] = []
    seen_combinations: set[tuple[str, str]] = set()
    for key, entry in sorted(_ANALYSIS_REGISTRY.items()):
        combo = (entry.model, entry.supernova_selection)
        if combo in seen_combinations:
            issues.append(f"{key}: duplicate model/SN combination")
        seen_combinations.add(combo)
        if entry.evidence_tier != "published_external":
            issues.append(f"{key}: official chain must be published_external")
        if not entry.source_url.startswith(DESI_DR2_CHAIN_ROOT_URL + "/cobaya/"):
            issues.append(f"{key}: non-official source URL")
        if entry.license_name != DESI_DATA_LICENSE:
            issues.append(f"{key}: unexpected data license")
        if entry.weight_column != "weight":
            issues.append(f"{key}: unexpected weight column")
        if entry.paper_arxiv != "2503.14738":
            issues.append(f"{key}: unexpected DR2 results paper")
        if entry.paper_doi != "10.1103/tr6y-kpc6":
            issues.append(f"{key}: unexpected DR2 results DOI")
        param_map = dict(entry.parameter_map)
        expected = {"w": "w", "H0": "H0", "omegam": "omegam"}
        if entry.model == "w0wa_cdm":
            expected = {
                "w0": "w",
                "wa": "wa",
                "H0": "H0",
                "omegam": "omegam",
            }
        if param_map != expected:
            issues.append(f"{key}: invalid parameter map")
        chain_artifacts = [a for a in entry.artifacts if a.role == "chain"]
        config_artifacts = [a for a in entry.artifacts if a.role == "config"]
        checkpoint_artifacts = [a for a in entry.artifacts if a.role == "checkpoint"]
        reference_artifacts = [
            a for a in entry.artifacts if a.role == "reference_summary"
        ]
        if len(chain_artifacts) != 4:
            issues.append(f"{key}: expected four chain artifacts")
        if len(config_artifacts) != 1:
            issues.append(f"{key}: expected one config artifact")
        if len(checkpoint_artifacts) != 1:
            issues.append(f"{key}: expected one checkpoint artifact")
        if len(reference_artifacts) != 1:
            issues.append(f"{key}: expected one reference summary artifact")
        paths = [artifact.relative_path for artifact in entry.artifacts]
        if len(paths) != len(set(paths)):
            issues.append(f"{key}: duplicate artifact path")
        for artifact in entry.artifacts:
            if not _SHA256_RE.fullmatch(artifact.sha256):
                issues.append(f"{key}: invalid sha256 for {artifact.relative_path}")
            if not artifact.relative_path.startswith(
                f"cobaya/{entry.official_model_folder}/"
            ):
                issues.append(f"{key}: artifact path/model mismatch")
        if entry.supernova_selection == "pantheon_plus":
            note = entry.notes.lower()
            if "uncalibrated" not in note or "sh0es-calibrated" not in note:
                issues.append(f"{key}: Pantheon+ calibration distinction missing")
    if not _SHA256_RE.fullmatch(DESI_DR2_CHAIN_MANIFEST_SHA256):
        issues.append("invalid official manifest sha256")
    return issues
