"""W2 (PART W): SIMBAD otype allow-list — 非河外对象的 rvz_redshift
不再作为 cosmological z 展示。

修复 B4 Pleiades 回归: otype_txt="Open (galactic) Cluster" 让 legacy
substring-matching 的 `_is_galactic_stellar_type` 漏判, 导致 z=2.01e-05
(实际是 radial_velocity/c 的内部编码) 被当 cosmological redshift 展示。

新 `_otype_is_extragalactic` 走 allow-list — 只明确的 otype code
({G, QSO, AGN, Sy1, Sy2, Bla, BCG, GrG, LSB, ...}) 才保留 z, 其它默认
剥离。对 SIMBAD 未来新增 otype 更安全。
"""
from __future__ import annotations

from app.connectors.simbad import _otype_is_extragalactic


# ---- allow-list 基本行为 ----


def test_otype_galactic_stellar_stripped():
    """恒星 otype 不允许 cosmological z."""
    assert not _otype_is_extragalactic("*")
    assert not _otype_is_extragalactic("**")  # 双星
    assert not _otype_is_extragalactic("V*")  # 变星
    assert not _otype_is_extragalactic("Cepheid")
    assert not _otype_is_extragalactic("WD*")


def test_otype_cluster_stripped():
    """疏散星团 / 球状星团都不允许 cosmological z (B4 核心 bug)."""
    assert not _otype_is_extragalactic("OpC")  # Pleiades
    assert not _otype_is_extragalactic("GlC")
    assert not _otype_is_extragalactic("Cl*")


def test_otype_mw_objects_stripped():
    """MW 系内对象全部剥离 z (SNR / HII / Neb / PN / Psr)."""
    assert not _otype_is_extragalactic("SNR")
    assert not _otype_is_extragalactic("HII")
    assert not _otype_is_extragalactic("Neb")
    assert not _otype_is_extragalactic("PN")
    assert not _otype_is_extragalactic("Psr")
    assert not _otype_is_extragalactic("MolCld")


def test_otype_none_and_empty_stripped():
    """空 / None / 未知 otype 默认 galactic (更安全)."""
    assert not _otype_is_extragalactic(None)
    assert not _otype_is_extragalactic("")
    assert not _otype_is_extragalactic("   ")
    assert not _otype_is_extragalactic("UnknownType99")


# ---- allow-list 河外类型应放行 ----


def test_otype_galaxy_allowed():
    """通用星系类型允许 z."""
    assert _otype_is_extragalactic("G")
    assert _otype_is_extragalactic("GiC")
    assert _otype_is_extragalactic("GiG")
    assert _otype_is_extragalactic("GrG")
    assert _otype_is_extragalactic("ClG")


def test_otype_special_galaxy_allowed():
    """特殊星系类型 (LSB / BCG / EmG / SBG / LINER) 允许 z."""
    assert _otype_is_extragalactic("LSB")
    assert _otype_is_extragalactic("BCG")
    assert _otype_is_extragalactic("EmG")
    assert _otype_is_extragalactic("SBG")
    assert _otype_is_extragalactic("H2G")


def test_otype_agn_allowed():
    """AGN 类 (Sy1/2 / QSO / Bla) 允许 z."""
    assert _otype_is_extragalactic("AGN")
    assert _otype_is_extragalactic("Sy1")
    assert _otype_is_extragalactic("Sy2")
    assert _otype_is_extragalactic("QSO")
    assert _otype_is_extragalactic("Bla")
    assert _otype_is_extragalactic("BLL")
    assert _otype_is_extragalactic("BLLac")
    assert _otype_is_extragalactic("rG")


def test_otype_lensing_allowed():
    """引力透镜 / GW 事件允许 z."""
    assert _otype_is_extragalactic("Lev")
    assert _otype_is_extragalactic("LeG")
    assert _otype_is_extragalactic("LeQ")
    assert _otype_is_extragalactic("grv")
    assert _otype_is_extragalactic("GWE")


def test_otype_case_sensitivity():
    """Strip 空格但严格大小写 (SIMBAD otype code 本来就 case-sensitive)."""
    assert _otype_is_extragalactic("QSO")
    assert not _otype_is_extragalactic("qso")  # 小写不是 SIMBAD 标准
    assert _otype_is_extragalactic("  G  ")  # 空格 strip
