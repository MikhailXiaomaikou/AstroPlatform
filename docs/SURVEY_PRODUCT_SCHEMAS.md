# Rubin, Euclid, and Roman Schema Fixtures

## What this milestone does

This P1 milestone gives Standard Astro one common shape for describing a
future survey product. Think of it as a checked form that records what a table
*would need* before the platform can safely use it: the release, columns and
units, coordinate and time systems, redshift type, covariance, masks,
selection, coverage, checksums, access rules, licence, and permitted claim
scope.

It does **not** connect to a survey archive. A passing fixture check only says
that a logical test record fits this form. It does not prove that an archive
row exists, and it cannot support a cosmological measurement.

## Maturity levels

| Level | Plain-language meaning | Current use |
|---|---|---|
| `SCHEMA_FIXTURE_ONLY` | We have a local test shape, but no exact physical table and file checksum. | Rubin, Euclid, Roman |
| `SOURCE_PINNED` | An exact released table/export, column map, version, licence, and SHA-256 are fixed. | None |
| `EXECUTABLE` | The pinned source also has a reviewed connector and scientific validation. | Forbidden in this P1 milestone |

The registry fails closed: every current adapter returns
`SURVEY_PRODUCT_NOT_EXECUTABLE`, `CAPABILITY_GAP`,
`publication_ready=false`, and `__do_not_claim__=true` for execution.

## Status checked on 2026-07-17

| Survey | Official status used by the fixture | Why it remains fixture-only |
|---|---|---|
| Rubin | [EDP2 is planned for 2026-07-27](https://rubinobservatory.org/events/edp2-release), ten days after the check date. | No released EDP2 physical table, column map, mask/selection record, or product checksum is pinned. |
| Euclid | [Q1 is released](https://euclid.esac.esa.int/dr/q1/) and has a versioned [Q1 DPDD 2.0](https://euclid.esac.esa.int/dr/q1/dpdd/frontpage.html). | The fixture does not select and mirror one physical Q1 catalogue export, so it has no product checksum or exact column mapping. The official [Q1 supplement](https://euclid.esac.esa.int/dr/q1/expsup/master.html) is cited for the declared 63.1-square-degree coverage and product families only. |
| Roman | NASA's [2026-01-29 status](https://science.nasa.gov/missions/roman-space-telescope/building-roman/) is pre-launch. | Planning documents describe future product families, not released flight products. No flight catalogue, mask, selection function, or checksum exists in this registry. |

These pages are live external sources. Their status must be checked again on
the execution date; the local `checked_utc` field is a record of the last
review, not a promise that the web page will never change.

## Shared adapter contract

Every fixture records:

- release name, version, status, official URL, and check time;
- logical fields with data types and units;
- coordinate frame and longitude/latitude fields;
- time field, scale, format, and reference position;
- redshift types, value fields, and uncertainty fields;
- covariance, mask, and selection-product status;
- spatial/temporal coverage and its source;
- SHA-256 policy and the currently missing data-product hash;
- authentication, rate-limit, archive, and offline behavior;
- licence status and official sources;
- a metadata-only supported claim scope and explicit limitations.

The logical names (`object_id`, `ra`, `dec`, and so on) are internal fixture
names. They are **not guessed archive columns**. A future source-pinning change
must map each logical name to an exact physical release column.

## Promotion checklist

To promote one product to `SOURCE_PINNED`:

1. Recheck the official release and licence pages on that day.
2. Choose one exact table or immutable export; do not register a moving search
   result.
3. Record the release and schema versions and every physical column name/unit.
4. Pin coordinate and time conventions, redshift semantics, masks, selection,
   and covariance needed by the intended claim.
5. Download through an operator-controlled process and record the product's
   SHA-256.
6. Add a fixture that proves checksum, field-name, unit, and schema drift all
   fail closed.
7. Run the registry audit and obtain science review.

The promotion audit accepts no silent `unknown` values in these science
sections. Coordinate mapping must be pinned. Time, redshift, coverage,
covariance, mask, and selection must each be either `pinned` (with its physical
field or artifact) or explicitly `reviewed_not_applicable`. An artifact must
use an allowlisted official archive host and carry its own fixed version and
SHA-256; an arbitrary URL is not a pin. Authentication, rate-limit policy,
product licence, checksum scope, and official document versions must also be
resolved.

Promotion to `EXECUTABLE` is a later, separate change. It additionally needs a
reviewed archive connector, authentication/rate-limit handling, provenance,
real-source tests, and claim-specific scientific validation.

## Local verification

From `backend/`:

```bash
./venv/bin/python scripts/audit_survey_product_registry.py
./venv/bin/pytest tests/test_survey_product_registry.py -q --no-cov
```

The main cosmology registry audit also invokes this survey audit, so fixture
drift blocks the existing scientific CI gate.
