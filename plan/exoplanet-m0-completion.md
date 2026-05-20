# Exoplanet M0 Completion Report

仓库: `astro-platform`
分支: `main`
时间窗: 2026-05-20 启动 → 2026-05-21 完整收尾
状态: ✅ Shipped (3rd active module)

---

## 1. Context

This is the **3rd active research module** on the astro-platform after
`cosmology` and `solar_system`. Activating it serves three goals:

1. **Validate 6-layer template re-use a 3rd time** (Karpathy 三相似才抽象 临界).
   Solar-system M0 verified the template worked once; exoplanet M0 confirms it
   generalises to a third, independent domain (transit + RV physics + TESS data).
   The ModuleRegistry abstraction decision is deferred to the next iteration —
   collect more usage data before locking in a generic interface.
2. **Cover a high-traffic science domain.** Exoplanets dominate modern public-interest
   astronomy (TESS / JWST / habitable zones / TRAPPIST-1). Having a focused module
   means BYOK chat users with exoplanet questions get the same provenance + zero-
   fabrication guarantees as cosmology users.
3. **Reuse `lightkurve` and existing platform infrastructure.** TESS light-curve
   download via lightkurve already lived in the stellar / cosmology paths; we
   wrap it in `fetch_tess_lightcurve` to expose it under exoplanet focus too.

This document mirrors `plan/solar-system-m0-completion.md` — same 8 sections.

---

## 2. 落地清单

### 2.1 4 个 commit (本次 sprint)

| Hash | 主题 |
|---|---|
| `ede9d68` | fix(blind_test): A2 Apophis prompt 加明确两步走措辞 |
| `fb1bf39` | feat(exoplanet): 3rd active module — 完整 6-layer + ai_tools + Panel routing (M0) |
| `8349fa5` | feat(frontend): 4 个泛用 Panel 组件 + ChatPage Panel routing for 20 solar_system + exoplanet 工具 |
| (pending) | docs(exoplanet): M0 报告 + CHANGELOG 段 |

### 2.2 8 个 LLM-callable tools

| 工具 | 类型 | 数据源/算法 | Reference |
|---|---|---|---|
| `query_exoplanet_archive` | data fetch | NASA Exoplanet Archive pscomppars | Akeson+ 2013 PASP 125, 989 |
| `query_confirmed_planets` | data fetch | NASA Archive TAP WHERE filter | Akeson+ 2013 |
| `fetch_tess_lightcurve` | data fetch | MAST via lightkurve | Ricker+ 2015 JATIS 1, 014003 |
| `query_tess_target_list` | data fetch | TIC v8 via astroquery.mast | Stassun+ 2019 AJ 158, 138 |
| `fit_transit` | compute | Trapezoidal fast fit (Nelder-Mead) | Mandel & Agol 2002 ApJ 580 L171 |
| `compute_equilibrium_temperature` | compute | Stellar radiation balance + Bond albedo | Seager & Mallén-Ornelas 2003 |
| `compute_transit_depth` | compute | Geometric (R_p / R_star)² | Seager & Mallén-Ornelas 2003 |
| `compute_planet_density` | compute | Mean ρ = 3M / (4πR³) | Seager & Mallén-Ornelas 2003 |

### 2.3 File-level inventory

**New (10):**
```
backend/app/connectors/nasa_exoplanet_archive.py        (164 行)
backend/app/services/exoplanet_physical.py              (182 行)
backend/app/services/exoplanet_transit.py               (128 行)
backend/app/services/ai_tools_exoplanet.py              (497 行)
backend/app/prompts/modules/exoplanet/appendix.md       (15 行,新)
backend/scripts/blind_test_exoplanet_m0/{runner.py, cases.yaml, .gitignore}
backend/tests/test_exoplanet_services.py                (161 行,16 tests)
backend/tests/test_ai_tools_exoplanet.py                (184 行,13 tests)
```

**Modified (cross-cutting):**
- `backend/app/services/prompt_loader.py` (focus 分支)
- `backend/app/api/chat.py` (`_FOCUS_GATED_VALUES` + deadline)
- `backend/app/services/ai_tools.py` (4 lines glue)
- `backend/app/services/result_provenance.py` (8 tools classified)
- `backend/app/services/claim_validator.py` (5 numeric patterns + 8 bibcodes + chain)
- `backend/app/connectors/{availability,registry}.py`
- `backend/app/services/provenance_v2/fallback_registry.yaml` (+ NEA entry)
- `backend/app/prompts/modules/exoplanet/{manifest.yaml, prompt.md}` (mv from `_dormant_`)

**Frontend (5):**
- `frontend/src/components/viz/{TablePanel, WarningCard, PlotlyXYPanel, BarChartPanel}.tsx` (4 generic panels)
- `frontend/src/pages/Chat/ChatPage.tsx` (labels + icons + routing for **all 20 solar_system + exoplanet tools**)

---

## 3. Architecture contracts (re-validated)

### 3.1 6-Layer template re-use ✓ (3rd time, ModuleRegistry threshold reached)

| 层 | Files modified | exoplanet 工作 |
|---|---|---|
| L1 focus 开关 | `chat.py:_filter_tools_by_research_focus` + `prompt_loader.py` | `_FOCUS_GATED_VALUES` 加 "exoplanet"; `_active_module_names` 加分支 |
| L2 connectors | `connectors/nasa_exoplanet_archive.py` + `availability.py` + `registry.py` | NEA TAP query via astroquery.ipac.nexsci; emit `_provenance_dataset` |
| L3 service modules | `services/exoplanet_{physical,transit}.py` | Pure functions + full literature docstrings |
| L4 ai_tools 集中 | `ai_tools_exoplanet.py` + 4 lines glue in `ai_tools.py` | 8 tools centralized; main file un-bloated |
| L5 provenance + deadline | `result_provenance._{DATA,COMPUTE}_TOOLS` + `chat.py:_TOOL_DEADLINE_TABLE` | 4+4 classification + 4 deadlines (TESS 120s) |
| L6 prompt + validator | `modules/exoplanet/{manifest,prompt,appendix}.md` + `claim_validator` | 91-line prompt + 8 keystone bibcodes + 5 numeric patterns |
| L7 frontend | `i18n` (skipped this sprint) + `ChatPage` labels/icons + Panel routing | 4 generic Panel components |

**Karpathy threshold reached** — 3rd hardcoded `if focus == "exoplanet"` branch.
Next module should consider abstracting to a generic ModuleRegistry. Documented
in plan file for future iteration.

### 3.2 Zero-fabrication defenses (inherited, no changes)

The three layers from cosmology apply automatically:
1. Upstream `__tool_status__` / `__do_not_claim__` banners via `result_provenance.normalize_tool_result`
2. `data_source: enum["archive_cache", "user_supplied"]` on compute tools
3. `claim_validator` numeric universe + bibcode pool harvested from tool results

### 3.3 Key design decisions

- ❌ **No batman / pytransit**: M0 transit fitting uses trapezoidal Nelder-Mead
  (simplified, fast). For limb-darkened publication-grade fits, prompt explicitly
  recommends batman/pytransit downstream.
- ❌ **No RV fitting**: `fit_rv_orbit` exists in the legacy `ai_tools.py` but is
  not actively maintained for exoplanet workflows. Kept in manifest for backward
  compat; refer users to radvel for new RV work.
- ❌ **No 12 dedicated panels**: 4 generic frontend panels (TablePanel /
  PlotlyXYPanel / WarningCard / BarChartPanel) map 20 tools across the
  solar_system + exoplanet modules. Karpathy 三相似抽象。

---

## 4. Testing

### 4.1 Unit tests (29 new)

- `test_exoplanet_services.py` (16 tests): T_eq prototype (Earth 254K, hot
  Jupiter), transit depth Earth=84ppm, density Earth=5.5 / Jupiter=1.33,
  Kepler 3rd law, transit fit injection recovery, invalid input validation.
- `test_ai_tools_exoplanet.py` (13 tests): schema count, TOOLS registration,
  result_provenance classification, deadline table coverage, compute tool
  happy + failure paths, user_supplied downgrade, dispatch routing, claim_validator
  bibcodes + numeric patterns + chain.

**Regression sweep result (2026-05-20):** 365 tests passed across solar_system +
exoplanet + cosmology + platform when all modules loaded.

### 4.2 End-to-end blind test

`backend/scripts/blind_test_exoplanet_m0/` — 20 cases in 5 groups, mirroring
solar_system blind-test design:
- A 金路径 (5): HD 209458 b / TRAPPIST-1 e / population query / Earth-like depth / Kepler-22 b
- B 反幻造攻击 (5): radius fabrication / Mandel-Agol citation without tool /
  user_supplied lie / wrong-focus tool / sandbox bypass
- C abstention (3): out-of-focus (cosmology) / non-existent planet / 100-yr LC
- D 优先级 (2): confirmed vs candidate / archive pl_eqt vs recompute
- E 多工具链 (5): WASP-12 b full brief / habitable-zone survey / planet
  density classification / TESS transit workflow / TRAPPIST-1 full system

(Results will be added in §5 once the blind test completes.)

---

## 5. 一轮盲测综合效果(待填,跑完后更新)

<!-- TODO: 一轮盲测完成后 fill 此段 -->

---

## 6. 残留 / 未来计划

### 6.1 Known M0 limitations

- **Bond albedo not in NASA Archive**: `pl_eqt` from archive often computed
  with A=0; prompt explicitly tells LLM to recompute with A=0.3/0.1 for
  Earth-like / gas-giant cases. Could automate this in `compute_equilibrium_temperature`
  if a `from_archive=true` flag is added.
- **Transit fit is trapezoidal not Mandel-Agol limb-darkened**: For accurate
  R_p / R_star with limb darkening, recommend batman/pytransit downstream.
  Adding batman dependency is M1+ work.
- **No frontend i18n**: ChatPage `labels` dict is hardcoded English; making
  it focus-aware requires routing through `useI18n` which is a non-trivial
  refactor. M1+ work.
- **fit_rv_orbit not actively maintained**: legacy tool kept in manifest.
  For new RV work recommend radvel / the-joker.

### 6.2 M1 candidates

- **batman / pytransit integration**: replace trapezoidal fit with limb-darkened
  Mandel-Agol model. ~3-5 hours.
- **Bond albedo derivation from secondary eclipse**: new tool
  `compute_bond_albedo_from_secondary_eclipse` for hot-Jupiter community.
- **Frontend i18n routing**: refactor ChatPage labels through `useI18n`,
  adding `cmd.<tool_name>` keys × 4 lang.
- **ModuleRegistry abstraction**: now that 3 active modules exist, design and
  implement a generic registry to replace the 3 hardcoded `if focus ==` branches.
  Needs careful API design — see Karpathy rule of three discussion.

### 6.3 Out of scope (永远)

- Direct stellar variability / starspot modeling → stellar module
- Microlensing → independent module
- Planetary atmospheres / transmission spectroscopy → M2+ if user demand emerges

---

## 7. Acknowledgements

- **astroquery** (Ginsburg+ 2019 AJ 157, 98) — NASA Exoplanet Archive +
  MAST TIC wrappers
- **lightkurve** (lightkurve Collaboration 2018) — TESS light-curve download
- **NASA Exoplanet Archive team** (Akeson+ 2013) — authoritative confirmed-planet
  composite parameters
- **TESS mission team** (Ricker+ 2015) — all-sky transit survey
- **batman / pytransit** authors — explicitly recommended for limb-darkened
  publication fits (we don't depend on them in M0 but cite them)

---

## 8. 验证命令

```bash
# 单元 + 集成 (本地无 LLM)
cd backend && source .venv/bin/activate
pytest tests/test_exoplanet_services.py tests/test_ai_tools_exoplanet.py -q --no-cov

# 端到端盲测 (需要 OPENAI_CLI 或 ANTHROPIC_API_KEY)
ASTRO_RESEARCH_FOCUS=exoplanet OPENAI_CLI_ENABLED=1 \
  python scripts/blind_test_exoplanet_m0/runner.py --provider local

# Frontend build
cd ../frontend && npm run build
```
