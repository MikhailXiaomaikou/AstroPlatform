"""Stage 4 — 论文 mining spot-check 子项目 (2026-05-19)

学术造假风险背景: 平台的 `extract_literature_tables` / `mine_paper_tools` 工具
完全 trust 论文报告值, 没有任何 sanity check. 如果论文的数字是 cherry-pick /
p-hack / 杜撰的, AI 会直接 mine 后塞给用户. 本模块给关键 cosmology 量加一层
"value 一致性 spot-check": 论文 cite 一个值 → 平台从 vendored archive 数据重算
→ 对比. 不一致 → flag.

设计约束 (用户硬约束, 2026-05-19):
- Opt-in: 只在 env `LITERATURE_SPOT_CHECK_ENABLED=true` 时生效
- Warn-only: spot-check fail 只 flag, 不 hard-block AI 调用
- 不动 `cosmology_likelihoods.py` 核心逻辑, 只独立读 vendored npz

支持的 spot-check 类型 (MVP):
1. SN distance modulus — 查 Pantheon+SH0ES 2022 1701 SN catalog
2. CMB compressed parameter (H0/Ωm/σ8/S8) — 跟 Planck 2018 baseline 一致性比对

不支持 (Phase 3+):
- Cepheid 距离梯子 (需 HST + Gaia 多源)

Stage 4 Phase 2 (2026-05-19 Stage 6 P0c-D) 新增:
- BAO 距离比 (DM/rd / DH/rd / DV/rd) vs DESI 2024 DR1 (arXiv:2404.03002)
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np


_PANTHEON_PLUS_NPZ = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "pantheon_plus_2022"
    / "data.npz"
)

_DESI_2024_BAO_CSV = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "desi_2024_bao.csv"
)


# Planck 2018 compressed ΛCDM posterior summary
# 来源: backend/app/services/cosmology_likelihoods.py:590-607
# (Planck Collaboration VI 2020 Table 2 baseline)
# 这里独立 hardcode 一份避免循环 import 和动核心模块.
# 单元测试 assert 跟 likelihoods.py 一致, 任何 drift 立即被发现.
PLANCK_2018_BASELINE: dict[str, tuple[float, float]] = {
    "H0":     (67.36,  0.54),
    "omegam": (0.3153, 0.0073),
    "sigma8": (0.8111, 0.0060),
    "S8":     (0.832,  0.013),
}

# 常见别名 → 规范名映射 (AI / 论文里 H0 可能写成 H_0 / Hubble constant / etc.)
_CMB_PARAM_ALIASES: dict[str, str] = {
    "h0": "H0", "h_0": "H0", "hubble": "H0", "hubble_constant": "H0",
    "omegam": "omegam", "omega_m": "omegam", "om0": "omegam",
    "matter_density": "omegam", "ωm": "omegam",
    "sigma8": "sigma8", "sigma_8": "sigma8", "σ8": "sigma8",
    "s8": "S8", "s_8": "S8",
}


SpotCheckStatus = Literal["passed", "failed", "unavailable"]


@dataclass
class SpotCheckResult:
    """spot-check 结果. 序列化后塞进 tool_result, frontend 用来渲染 banner."""
    status: SpotCheckStatus
    passed: bool           # True 仅当 status == "passed"
    our_value: float | None
    paper_value: float | None
    sigma_distance: float | None
    margin_sigma: float
    source: str            # "pantheon_plus_2022" / "planck_2018_baseline" / etc.
    quantity_type: str     # "sn_distance_modulus" / "cmb_H0" / etc.
    target: str            # SN name or param name
    reason: str            # 简短原因, e.g. "value within 1.2σ" / "SN not in sample"
    disclaimer: str = (
        "Spot-check verifies value is consistent with vendored archive data, "
        "NOT a full peer review. Reduction-level fabrication cannot be detected."
    )

    def to_dict(self) -> dict:
        return asdict(self)


def is_spot_check_enabled() -> bool:
    """opt-in flag. 默认 False, 等同现有行为不影响整体."""
    return os.environ.get("LITERATURE_SPOT_CHECK_ENABLED", "").lower() == "true"


@lru_cache(maxsize=1)
def _load_pantheon_cid_data() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """独立 loader (不复用 cosmology_likelihoods.py 的 loader, 避免动核心).
    返回 (cid, mu, mu_err_diag).
    """
    if not _PANTHEON_PLUS_NPZ.exists():
        raise FileNotFoundError(
            f"Pantheon+ data not vendored at {_PANTHEON_PLUS_NPZ}. "
            "Run `python scripts/fetch_pantheon_plus.py` to regenerate."
        )
    data = np.load(_PANTHEON_PLUS_NPZ, allow_pickle=False)
    if "cid" not in data.files:
        raise KeyError(
            "Pantheon+ npz is missing 'cid' column. Re-run "
            "`python scripts/fetch_pantheon_plus.py` (extended in Stage 4 to "
            "include CID for spot-check)."
        )
    return data["cid"], data["mu"], data["mu_err_diag"]


def check_sn_distance_modulus(
    sn_name: str,
    claimed_mu: float,
    margin_sigma: float = 3.0,
) -> SpotCheckResult:
    """Spot-check SN distance modulus against Pantheon+SH0ES 2022 catalog.

    同一 SN 可能在多个 survey 里出现 (e.g. SN 2011fe 在 IDSURVEY=51 和 56 都有),
    用 inverse-variance weighted mean 聚合多 row, 然后做 |claimed - our| / our_err
    一致性检查.

    Args:
        sn_name: Pantheon+ CID, e.g. "2011fe"
        claimed_mu: 论文报告的 distance modulus
        margin_sigma: 阈值, 超过则 status="failed". 默认 3σ.

    Returns:
        SpotCheckResult — status ∈ {passed, failed, unavailable}
    """
    try:
        cid, mu_all, mu_err_all = _load_pantheon_cid_data()
    except (FileNotFoundError, KeyError) as exc:
        return SpotCheckResult(
            status="unavailable",
            passed=False,
            our_value=None,
            paper_value=claimed_mu,
            sigma_distance=None,
            margin_sigma=margin_sigma,
            source="pantheon_plus_2022",
            quantity_type="sn_distance_modulus",
            target=sn_name,
            reason=f"Pantheon+ data unavailable: {exc}",
        )

    mask = (cid == sn_name)
    if mask.sum() == 0:
        return SpotCheckResult(
            status="unavailable",
            passed=False,
            our_value=None,
            paper_value=claimed_mu,
            sigma_distance=None,
            margin_sigma=margin_sigma,
            source="pantheon_plus_2022",
            quantity_type="sn_distance_modulus",
            target=sn_name,
            reason=(
                f"SN '{sn_name}' not found in Pantheon+SH0ES 2022 1701-SN sample "
                f"(1543 unique SNe). Could be naming convention mismatch or SN "
                f"outside Pantheon+ selection."
            ),
        )

    # Inverse-variance weighted mean across survey rows for the same SN
    mus = mu_all[mask].astype(np.float64)
    errs = mu_err_all[mask].astype(np.float64)
    weights = 1.0 / (errs ** 2)
    our_mu = float((mus * weights).sum() / weights.sum())
    our_err = float(1.0 / np.sqrt(weights.sum()))

    sigma_distance = abs(claimed_mu - our_mu) / our_err
    passed = sigma_distance <= margin_sigma

    n_rows = int(mask.sum())
    return SpotCheckResult(
        status="passed" if passed else "failed",
        passed=passed,
        our_value=our_mu,
        paper_value=claimed_mu,
        sigma_distance=sigma_distance,
        margin_sigma=margin_sigma,
        source=f"pantheon_plus_2022 ({n_rows} survey row{'s' if n_rows > 1 else ''})",
        quantity_type="sn_distance_modulus",
        target=sn_name,
        reason=(
            f"Claimed mu={claimed_mu:.3f}, our mu={our_mu:.3f} ± {our_err:.3f} "
            f"({sigma_distance:.2f}σ apart, threshold {margin_sigma:.1f}σ)"
        ),
    )


def check_cmb_compressed_value(
    param: str,
    claimed_value: float,
    margin_sigma: float = 3.0,
) -> SpotCheckResult:
    """Spot-check a compressed-CMB parameter against Planck 2018 baseline.

    严格说这不是真 spot-check (我们没独立 recompute CMB power spectrum), 是
    "value match check" — 验证论文 cite 的值是否跟 Planck 2018 公认基线一致.
    抓 "AI cite 的 H0=72 来自 non-CMB 测量但被当作 CMB-based" 类错误.

    Args:
        param: 支持 H0 / omegam / sigma8 / S8 (大小写不敏感, 接受常见别名)
        claimed_value: 论文/AI 报告的数值
        margin_sigma: 阈值. 默认 3σ.

    Returns:
        SpotCheckResult
    """
    normalized = _CMB_PARAM_ALIASES.get(param.lower().replace(" ", "_"))
    if normalized is None or normalized not in PLANCK_2018_BASELINE:
        return SpotCheckResult(
            status="unavailable",
            passed=False,
            our_value=None,
            paper_value=claimed_value,
            sigma_distance=None,
            margin_sigma=margin_sigma,
            source="planck_2018_baseline",
            quantity_type="cmb_compressed_parameter",
            target=param,
            reason=(
                f"Parameter '{param}' not supported. MVP supports: "
                f"{sorted(PLANCK_2018_BASELINE.keys())}"
            ),
        )

    mean, sigma = PLANCK_2018_BASELINE[normalized]
    sigma_distance = abs(claimed_value - mean) / sigma
    passed = sigma_distance <= margin_sigma

    return SpotCheckResult(
        status="passed" if passed else "failed",
        passed=passed,
        our_value=mean,
        paper_value=claimed_value,
        sigma_distance=sigma_distance,
        margin_sigma=margin_sigma,
        source="planck_2018_baseline",
        quantity_type=f"cmb_compressed_{normalized}",
        target=normalized,
        reason=(
            f"Claimed {normalized}={claimed_value:.4f}, Planck 2018 baseline="
            f"{mean:.4f} ± {sigma:.4f} ({sigma_distance:.2f}σ apart, "
            f"threshold {margin_sigma:.1f}σ). Note: this checks consistency with "
            f"the published Planck baseline, NOT independent CMB analysis."
        ),
    )


@lru_cache(maxsize=1)
def _load_desi_2024_bao() -> dict[tuple[float, str], tuple[float, float, str]]:
    """Load DESI 2024 DR1 BAO measurements from vendor CSV.

    Returns dict {(z_eff, quantity): (value, sigma, tracer)}. quantity is one
    of "DM_over_rd" / "DH_over_rd" / "DV_over_rd".
    """
    if not _DESI_2024_BAO_CSV.exists():
        raise FileNotFoundError(
            f"DESI 2024 BAO CSV not vendored at {_DESI_2024_BAO_CSV}"
        )
    data: dict[tuple[float, str], tuple[float, float, str]] = {}
    with open(_DESI_2024_BAO_CSV) as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#") or line.lower().startswith("z_eff"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                z_eff = float(parts[0])
                value = float(parts[2])
                sigma = float(parts[3])
            except ValueError:
                continue
            quantity = parts[1]
            tracer = parts[4]
            data[(z_eff, quantity)] = (value, sigma, tracer)
    return data


def check_bao_distance_ratio(
    z_eff: float,
    quantity: str,
    claimed_value: float,
    margin_sigma: float = 3.0,
    z_tolerance: float = 0.05,
) -> SpotCheckResult:
    """Stage 6 P0c-D (Stage 4 Phase 2): BAO 距离比 spot-check vs DESI 2024 DR1.

    工作流: 找 |z - z_eff_paper| <= z_tolerance 的最近 z_eff bin, 取它的
    DESI 值跟 claimed 比. 没找到 close bin → unavailable.

    Args:
        z_eff: 论文 cite 的 z_eff (e.g. 0.51)
        quantity: 必须是 "DM_over_rd" / "DH_over_rd" / "DV_over_rd"
        claimed_value: 论文报告的距离比值
        margin_sigma: 阈值, 默认 3σ
        z_tolerance: z_eff 匹配容差, 默认 0.05

    Returns:
        SpotCheckResult — status ∈ {passed, failed, unavailable}

    注意: 不处理 cross-correlation between DM/rd & DH/rd at same z_eff
    (DESI typical r ~ -0.4 to -0.5). 这是 ±σ heuristic, 不是真 χ² 检验.
    """
    valid_quantities = {"DM_over_rd", "DH_over_rd", "DV_over_rd"}
    if quantity not in valid_quantities:
        return SpotCheckResult(
            status="unavailable",
            passed=False,
            our_value=None,
            paper_value=claimed_value,
            sigma_distance=None,
            margin_sigma=margin_sigma,
            source="desi_2024_dr1_bao",
            quantity_type=f"bao_{quantity}",
            target=f"z={z_eff:.3f}",
            reason=(
                f"Quantity {quantity!r} not supported. Use one of: "
                f"{sorted(valid_quantities)}"
            ),
        )
    try:
        bao_data = _load_desi_2024_bao()
    except FileNotFoundError as exc:
        return SpotCheckResult(
            status="unavailable",
            passed=False,
            our_value=None,
            paper_value=claimed_value,
            sigma_distance=None,
            margin_sigma=margin_sigma,
            source="desi_2024_dr1_bao",
            quantity_type=f"bao_{quantity}",
            target=f"z={z_eff:.3f}",
            reason=f"DESI 2024 BAO data unavailable: {exc}",
        )
    candidates = [
        (z_paper, vs)
        for (z_paper, q), vs in bao_data.items()
        if q == quantity and abs(z_paper - z_eff) <= z_tolerance
    ]
    if not candidates:
        available_z = sorted({z for (z, q), _ in bao_data.items() if q == quantity})
        return SpotCheckResult(
            status="unavailable",
            passed=False,
            our_value=None,
            paper_value=claimed_value,
            sigma_distance=None,
            margin_sigma=margin_sigma,
            source="desi_2024_dr1_bao",
            quantity_type=f"bao_{quantity}",
            target=f"z={z_eff:.3f}",
            reason=(
                f"No DESI 2024 {quantity} measurement within Δz={z_tolerance} "
                f"of z={z_eff:.3f}. Available z_eff for this quantity: "
                f"{available_z}"
            ),
        )
    z_paper, (value, sigma, tracer) = min(
        candidates, key=lambda x: abs(x[0] - z_eff)
    )
    sigma_distance = abs(claimed_value - value) / sigma
    passed = sigma_distance <= margin_sigma
    return SpotCheckResult(
        status="passed" if passed else "failed",
        passed=passed,
        our_value=value,
        paper_value=claimed_value,
        sigma_distance=sigma_distance,
        margin_sigma=margin_sigma,
        source=f"desi_2024_dr1_bao ({tracer})",
        quantity_type=f"bao_{quantity}",
        target=f"z={z_eff:.3f}",
        reason=(
            f"Claimed {quantity}={claimed_value:.3f} at z={z_eff:.3f}, "
            f"DESI 2024 ({tracer}) at z={z_paper:.3f}: {value:.3f} ± {sigma:.3f} "
            f"({sigma_distance:.2f}σ apart, threshold {margin_sigma:.1f}σ). "
            "Note: cross-covariance between DM/rd and DH/rd at same z_eff is "
            "ignored in this ±σ heuristic."
        ),
    )
