# Solar System Objects Module — asteroids + comets

**Status**: active (M0 Commit 6, 2026-05-18). 加载于 ASTRO_RESEARCH_FOCUS=solar_system.

This module exposes 12 LLM-callable tools for small-body planetary astronomy,
backed by 4 service modules (phot / thermo / taxonomy / dynamics) and 2
provenance-v2 connectors (jpl / mpc). 工具表见末尾 Citation table.

---

## 1. Designation 命名陷阱

小行星 / 彗星允许多种合法 designation, JPL Horizons / MPC / SBDB 都接受:

- `(3200) Phaethon` — IAU number + name
- `Phaethon` — name only (numbered NEAs / MBAs)
- `1983 TB` — provisional designation (asteroid)
- `2024 YR4` — provisional (NEA)
- `99942` — number only (Apophis)
- `C/2014 UN271` — comet provisional
- `67P/Churyumov–Gerasimenko` — periodic comet

**MUST NOT** do:
- `query_gaia_cluster(center_name="Phaethon")` — Phaethon 是 moving target, 不
  是 fixed-coord stellar cluster. 它在不同时间有不同 RA/Dec.
- `name_resolver.resolve_name("Phaethon")` — name_resolver 把它解成某个 SIMBAD
  恒星(错的, 给 garbage RA/Dec).

**Correct** flow: 用 `query_mpc_orbit` (orbital elements) 或
`fetch_horizons_ephemeris` (positional time series).

---

## 2. 时间标尺 (time scale) 陷阱

- **JPL Horizons epochs** 默认 **TDB** (Barycentric Dynamical Time). 输出 surface
  layer 是 **UTC**. 引用任何时间必须显式标注 scale.
- 短期 ephemeris (< 1 day): UTC vs TDB 差 < 1 min, 可忽略.
- 长期 propagation (> 10 yr): TAI-TDB drift 不可忽略, 必须用 TDB.
- 数字举例:
  - "2026-04-13 21:46 UTC" ✓ (close-approach time, UTC scale)
  - "JD 2461138.5 TDB" ✓ (precise epoch, TDB scale)
  - "2026-04-13 21:46" ✗ (timestamp 没 scale annotation)

---

## 3. End-to-end workflows

### 3.1 Asteroid ephemeris + brightness prediction

User: *"查 (3200) Phaethon 的轨道根数和 2026 年的星历, 并按 H-G 模型预估亮度变化"*

正确 tool chain:
1. `query_mpc_orbit("Phaethon")` → orbital elements + H, G
2. `fetch_horizons_ephemeris("Phaethon", start="2026-01-01", stop="2026-12-31", step="1d")`
   → time series of RA/Dec/V/r_au/Δ_au/phase_angle
3. `compute_hg_magnitude(H=14.6, G=0.15, phase_angles_deg=[...from step 2],
                         r_au=[...], delta_au=[...], data_source="archive_cache")`
4. Reply: V curve plot + Bowell+ 1989 citation + reproducibility envelope

### 3.2 Comet dust activity (Afρ)

User: *"67P 在 2015 perihelion 时 m_V=12 在 5\" aperture, r=1.24 au, Δ=2.7 au, Afρ 是多少"*

Workflow:
1. (optional) `fetch_horizons_ephemeris("67P", "2015-08-01", "2015-09-01", "1d")` 验证 r, Δ
2. `compute_afrho(m_comet=12.0, r_au=1.24, delta_au=2.7, aperture_arcsec=5.0,
                  alpha_deg=phase_from_step1, data_source="archive_cache")`
3. Reply: Afρ + Halley-Marcus phase-corrected Afρ(0°) + A'Hearn+ 1984 citation

### 3.3 NEO impact risk assessment

User: *"2024 YR4 在 100 年内撞地球的概率多大?"*

**CRITICAL precedence**: 总是先 `query_sentry_risk`, 因为 Sentry-II 是权威 source.

Workflow:
1. `query_sentry_risk("2024 YR4")` — Monte Carlo 含 Yarkovsky / orbit uncertainty
2. If Sentry 给数字 → quote cumulative impact probability + Palermo/Torino scale
3. If response is `not_on_sentry_list` (404) → quote "Sentry-II 未列出, cumulative
   impact probability < 1e-10 — 该 NEO 当前无 virtual impactor"
4. `compute_neo_collision_probability` 仅作为"几何上限" 参考, **绝对不**直接 quote 它
   作为"actual impact probability". 那是 Öpik 几何上限, 真实 Sentry 数值通常小
   10⁴-10⁶ 倍.

### 3.4 NEATM thermal modeling

User: *"4 Vesta 12μm 测了 180 mJy, H=3.20, r=2.36, Δ=1.46, 估直径和反照率"*

Workflow:
1. `fit_neatm_diameter_albedo(H=3.20, observed_flux_jy=0.180, lambda_um=12.0,
                              r_au=2.36, delta_au=1.46, eta=1.4)`
2. **M0 注意**: 单波段拟合, 用 Harris 1998 D-H-p_V 关系约束. 多波段
   (e.g. WISE W3 + W4) 自洽 η fit 留 M2.
3. Reply: D_km + p_V + T_ss + Harris 1998 / Mainzer+ 2011 citations

### 3.5 Taxonomic classification

User: *"SDSS asteroid colors g-r=0.65, r-i=-0.05, i-z=-0.30, 是哪个类"*

Workflow:
1. `classify_asteroid_sdss_colors(u_g=1.85, g_r=0.65, r_i=-0.05, i_z=-0.30)`
2. Reply: best_class (e.g. "V") + typical p_V + Carvano+ 2010 citation
3. **M0 简化**: 9 main classes via Carvano 4-color nearest-center. 25-class
   Bus-DeMeo subclasses (Sa/Sk/Sl/Sq/Sr/Sv) 留 M2 PCA 实现.

Or, from spectrum:
1. `classify_asteroid_busdemeo(wavelengths_um=[...], reflectance=[...])` —
   内部 spectrum_to_features → nearest-class
2. Reply: 12 main types (C/B/X/D/T/S/Q/V/A/R/K/L) + DeMeo+ 2009 citation

### 3.6 Shape model lookup

User: *"21 Lutetia 有 DAMIT shape model 吗"*

Workflow:
1. `query_damit_shape_model("21")` → spin period, pole solution, model URL
2. If empty: honest abstention "DAMIT 没有 21 Lutetia 的 shape model"
3. Reply: cite Ďurech+ 2010 A&A 513, A46

---

## 4. Tool usage rules (MUST follow)

### 4.1 NEVER reimplement archive queries in `run_python`

DO NOT do this:
```python
# 错误示范 — 绕开 retry / provenance / cache
from astroquery.jplhorizons import Horizons
obj = Horizons(id="Phaethon", ...)
```

USE instead: `fetch_horizons_ephemeris` tool. Reasons:
- bypasses connector retry + throttle (Horizons 限速)
- bypasses provenance-v2 (`_provenance_dataset` 丢失 → citation 失败)
- bypasses connector cache (重复请求慢 + 浪费 Horizons quota)
- sandbox 可能不能 import astroquery.jplhorizons / 不能 reach JPL

同理: 不要在 `run_python` 里直调 `astroquery.mpc.MPC` 或 `requests.get("ssd-api.jpl.nasa.gov/...")`. 用对应 tool.

### 4.2 NEVER use stellar tools on moving solar-system targets

WRONG:
- `query_gaia_cluster(center_name="Phaethon")` — Phaethon 不是 cluster
- `name_resolver.resolve_name("3200 Phaethon")` — gives star, not asteroid
- `run_adql` 想找 Phaethon 的 photometry — Gaia 不专门做 small bodies

CORRECT: 用 solar_system 模块的工具 (query_mpc_orbit / fetch_horizons_ephemeris).

### 4.3 ALWAYS declare `data_source` for compute tools

`compute_hg_magnitude`, `compute_afrho`, `fit_neatm_diameter_albedo`,
`compute_neo_collision_probability`, `classify_asteroid_busdemeo`,
`classify_asteroid_sdss_colors` 都有 `data_source` 枚举字段:

- `archive_cache`: 输入数值 traced to a real archive tool's output this turn
  (query_mpc_orbit / fetch_horizons_ephemeris / SBDB output) — 输出 data_origin
  = `cached_real` (可作为 publication-grade 数值).
- `user_supplied`: 用户在 prompt 里直接给的 numbers, 没经过 archive — 输出
  data_origin = `user_uploaded` (降级, claim_validator 会标 user_supplied warning).

如果 H, G, r, Δ 不是 from archive call this turn, 必须用 `user_supplied`.

### 4.4 Sentry-II precedence rule

对于 NEO impact probability 类问题:
1. 总是先 `query_sentry_risk(designation)`.
2. 仅当 Sentry 404 (not on risk list) 或用户明确问"geometric upper bound" 时,
   才用 `compute_neo_collision_probability`.
3. 引用 Öpik 上限时必须明确标注 "geometric upper bound, not actual probability".

### 4.5 Range limits for `fetch_horizons_ephemeris`

Horizons 对大量 row queries 限速 + 限制返回大小. Reasonable defaults:
- `step="1d"`, range ≤ 5 yr (365 × 5 = 1825 rows)
- `step="1h"`, range ≤ 30 days (720 rows)
- `step="1m"`, range ≤ 1 day (1440 rows, close-approach detail)
- 超过这些范围 → 分批 query 或粗化 step

### 4.6 G slope default values (Bowell+ 1989)

| Taxonomy | typical G |
|----------|-----------|
| S-type (most NEAs / MBAs) | 0.15 |
| C-type (dark, carbonaceous) | 0.05 |
| V-type (basaltic, e.g. Vesta family) | 0.30 |
| D-type / Trojans | 0.10 |
| Unknown / default | 0.15 |

---

## 5. Citation table

引用 bibcode 前必须由对应 tool 在 *this turn* 返回 — claim_validator strict mode
会 hard-block 不在 tool_results 里的 bibcode (`PROVENANCE_VALIDATOR_HARDBLOCK=true`).

| Tool | Reference | bibcode |
|------|-----------|---------|
| `compute_hg_magnitude` | Bowell+ 1989 *Asteroids II* | `1989aste.conf..524B` |
| `fetch_horizons_ephemeris` | Giorgini+ 1996 BAAS 28, 1158 | `1996DPS....28.2504G` |
| `query_mpc_orbit` | IAU Minor Planet Center | (no single paper) |
| `query_sbdb_orbit`, `query_sbdb_close_approaches` | JPL SBDB / CAD API | (no single paper) |
| `query_sentry_risk` | JPL CNEOS Sentry-II (2021) | (no single paper) |
| `query_damit_shape_model` | Ďurech+ 2010 A&A 513, A46 | `2010A&A...513A..46D` |
| `compute_afrho` | A'Hearn+ 1984 AJ 89, 579 | `1984AJ.....89..579A` |
| `fit_neatm_diameter_albedo` | Harris 1998 Icarus 131, 291 / Mainzer+ 2011 ApJ 743, 156 | `1998Icar..131..291H` / `2011ApJ...743..156M` |
| `compute_neo_collision_probability` | Öpik 1951 PRIA 54A, 165 + Wetherill 1967 JGR 72, 2429 + Morbidelli+ 2002 Icarus 158, 329 | `2002Icar..158..329M` |
| `classify_asteroid_busdemeo` | DeMeo+ 2009 Icarus 202, 160 | `2009Icar..202..160D` |
| `classify_asteroid_sdss_colors` | Carvano+ 2010 A&A 510, A43 | `2010A&A...510A..43C` |

软件 + Python 库:
- astroquery: Ginsburg+ 2019 AJ 157, 98 — `2019AJ....157...98G`
- sbpy: Mommert+ 2019 JOSS 4, 1426 — `2019JOSS....4.1426M`
- REBOUND (M2+): Rein & Liu 2012 A&A 537, A128 — `2012A&A...537A.128R`

---

## 6. Solar system objects — Bowell+ 1989 H-G photometric system (核心)

For asteroids, comets, TNOs:

1. **Ephemeris**: JPL Horizons (Giorgini+ 1996 BAAS 28, 1158) —
   authoritative solar system ephemeris, access via `fetch_horizons_ephemeris`.
2. **Minor Planet Center (MPC)**: IAU official designation and orbit database,
   access via `query_mpc_orbit`.
3. **H-G magnitude system** (Bowell+ 1989 *Asteroids II*, Univ. Arizona Press):
   ```
   H(α) = H - 2.5 log₁₀[(1-G) Φ₁(α) + G Φ₂(α)]
   ```
   where α is phase angle, G is slope parameter (default 0.15).
4. **Proper vs osculating orbital elements**: osculating from Horizons, proper
   from AstDyS (Knežević & Milani 2003 CeMDA 85, 145) for dynamical family
   membership. (proper elements M2+)
5. **NEO collision probability**: Öpik 1951 Proc. Royal Irish Academy 54A, 165
   (modern formulations in Morbidelli+ 2002 Icarus 158, 329). 真实 NEO impact
   risk 走 JPL Sentry-II, 不要直接 quote Öpik 上限.

---

## 7. Solar-system honest abstention examples

当用户问超出 solar_system 模块 scope 的问题时, emit structured abstention 而非
凭训练数据回答:

- Pulsar timing: "Solar-system module 不含 pulsar timing 工具. ASTRO_RESEARCH_FOCUS=pulsar_timing 才有."
- Galaxy redshift: 不在 solar_system scope, 走 cosmology focus.
- 行星大气模型: M0 不含, 走 sbpy.activity.gas 或 PDS (M1+).
- TNO atmosphere / volatile chemistry: M0 不含, 留 M2+.

---

## 8. Module 工具速查

| Tool name | One-liner | Reference |
|-----------|-----------|-----------|
| `query_mpc_orbit` | MPC orbit + H, G | IAU MPC |
| `fetch_horizons_ephemeris` | JPL Horizons time series | 1996DPS....28.2504G |
| `query_sbdb_orbit` | SBDB 详细 orbit + 不确定度 | JPL SSD |
| `query_sbdb_close_approaches` | CAD API close approaches | JPL SSD |
| `query_sentry_risk` | Sentry-II 100yr impact (权威) | JPL CNEOS |
| `query_damit_shape_model` | DAMIT 3D 凸壳模型 | 2010A&A...513A..46D |
| `compute_hg_magnitude` | V(α, r, Δ) curve | 1989aste.conf..524B |
| `compute_afrho` | Afρ + Halley-Marcus phase corr. | 1984AJ.....89..579A |
| `fit_neatm_diameter_albedo` | NEATM D, p_V 单波段拟合 | 2011ApJ...743..156M |
| `compute_neo_collision_probability` | **Öpik 几何上限** (not Sentry) | 2002Icar..158..329M |
| `classify_asteroid_busdemeo` | 12-class taxonomy from features | 2009Icar..202..160D |
| `classify_asteroid_sdss_colors` | Carvano 9-class from SDSS | 2010A&A...510A..43C |
