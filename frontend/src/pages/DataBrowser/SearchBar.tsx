import { useState, useEffect } from "react";
import {
  getSpectralLines,
  type AdvancedSearchRequest,
  type SpectralLineInfo,
} from "../../api/client";
import { useI18n } from "../../i18n";

interface Props {
  onSearch: (query: string, sources: string[], radius: number) => void;
  onAdvancedSearch: (req: AdvancedSearchRequest) => void;
  loading: boolean;
}

const ALL_SOURCES = ["sdss", "gaia", "simbad", "vizier", "mast", "ned", "2mass", "chandra", "allwise", "alma", "eso", "irsa", "jwst", "lamost", "desi"];
// Provenance v2 rollout: only these connectors have archive_version +
// registry-backed citation support. Others are gated in the backend
// (backend/app/connectors/availability.py V2_AVAILABLE_CONNECTORS) and
// disabled here so the user can't manually select them either.
const V2_AVAILABLE_SOURCES = new Set(["vizier", "gaia", "simbad", "ned", "2mass", "alma"]);
const V2_AVAILABLE_LABEL = "vizier / gaia / simbad / ned / 2mass / alma";
const isSourceAvailable = (s: string): boolean => V2_AVAILABLE_SOURCES.has(s);
const DEFAULT_SOURCES = ["gaia", "simbad", "vizier"];
const DEFAULT_ADV_SOURCES = ["simbad", "ned", "2mass"];

const OBJECT_TYPES = [
  "AGN",
  "quasar",
  "galaxy",
  "star",
  "nebula",
  "pulsar",
  "supernova",
  "Lyman-alpha emitter",
  "submillimeter galaxy",
  "Lyman-break galaxy",
  "gamma-ray burst",
  "Seyfert galaxy",
  "blazar",
  "starburst galaxy",
  "white dwarf",
  "brown dwarf",
  "cepheid",
  "RR Lyrae",
  "galaxy cluster",
  "globular cluster",
];

const OBSERVATION_TYPES = [
  "spectroscopy",
  "imaging",
  "photometry",
  "interferometry",
  "polarimetry",
  "X-ray",
  "infrared",
  "near-infrared",
  "mid-infrared",
  "far-infrared",
  "submillimeter",
  "millimeter",
  "radio",
  "ultraviolet",
  "optical",
];

const SOURCE_TOOLTIPS: Record<string, string> = {
  simbad: "Known objects by name",
  gaia: "2B+ star positions & distances",
  sdss: "Galaxy images & spectra",
  mast: "HST, JWST, Kepler data",
  alma: "Submillimeter observations",
  vizier: "Catalog collection",
  ned: "Extragalactic database",
  "2mass": "Near-infrared survey",
  chandra: "X-ray observations",
  allwise: "All-sky infrared survey",
  eso: "ESO archive",
  irsa: "Infrared archive",
  jwst: "JWST observations",
  lamost: "Spectroscopic survey",
  desi: "Dark energy survey",
};

export default function SearchBar({ onSearch, onAdvancedSearch, loading }: Props) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"quick" | "advanced">("quick");

  // Quick search state
  const [query, setQuery] = useState("");
  const [sources, setSources] = useState<string[]>(DEFAULT_SOURCES);
  const [radius, setRadius] = useState(0.1);

  // Advanced search state
  const [naturalQuery, setNaturalQuery] = useState("");
  const [advSources, setAdvSources] = useState<string[]>(DEFAULT_ADV_SOURCES);
  const [advRadius, setAdvRadius] = useState(1.0);
  const [advRa, setAdvRa] = useState<string>("");
  const [advDec, setAdvDec] = useState<string>("");
  const [redshiftMin, setRedshiftMin] = useState<string>("");
  const [redshiftMax, setRedshiftMax] = useState<string>("");
  const [spectralLine, setSpectralLine] = useState<string>("");
  const [wavelengthMin, setWavelengthMin] = useState<string>("");
  const [wavelengthMax, setWavelengthMax] = useState<string>("");
  const [objectType, setObjectType] = useState<string>("");
  const [observationType, setObservationType] = useState<string>("");
  const [showFilters, setShowFilters] = useState(false);

  // Spectral lines from backend
  const [spectralLines, setSpectralLines] = useState<SpectralLineInfo[]>([]);

  useEffect(() => {
    getSpectralLines()
      .then(setSpectralLines)
      .catch(() => {
        // Use a minimal fallback if the API is unavailable
      });
  }, []);

  const toggleSource = (s: string) => {
    if (!isSourceAvailable(s)) return;  // gated under-maintenance source
    setSources((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    );
  };

  const toggleAdvSource = (s: string) => {
    if (!isSourceAvailable(s)) return;
    setAdvSources((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    );
  };

  const submitQuickSearch = (nextQuery = query) => {
    const trimmed = nextQuery.trim();
    if (!trimmed || sources.length === 0) return;
    onSearch(trimmed, sources, radius);
  };

  const handleQuickSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submitQuickSearch();
  };

  const handleSuggestionSelect = (value: string) => {
    setQuery(value);
    submitQuickSearch(value);
  };

  const handleQuickKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (!query.trim() || sources.length === 0) return;
    submitQuickSearch();
  };

  const handleAdvancedSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (advSources.length === 0) return;

    const req: AdvancedSearchRequest = {
      sources: advSources,
      radius: advRadius,
    };

    if (naturalQuery.trim()) req.natural_query = naturalQuery.trim();
    if (advRa) req.ra = parseFloat(advRa);
    if (advDec) req.dec = parseFloat(advDec);
    if (redshiftMin) req.redshift_min = parseFloat(redshiftMin);
    if (redshiftMax) req.redshift_max = parseFloat(redshiftMax);
    if (spectralLine) req.spectral_line = spectralLine;
    if (wavelengthMin) req.wavelength_min = parseFloat(wavelengthMin);
    if (wavelengthMax) req.wavelength_max = parseFloat(wavelengthMax);
    if (objectType) req.object_type = objectType;
    if (observationType) req.observation_type = observationType;

    // Must have at least a natural query or one filter
    const hasFilter =
      req.natural_query ||
      req.ra !== undefined ||
      req.redshift_min !== undefined ||
      req.redshift_max !== undefined ||
      req.spectral_line ||
      req.wavelength_min !== undefined ||
      req.wavelength_max !== undefined ||
      req.object_type ||
      req.observation_type;
    if (!hasFilter) return;

    onAdvancedSearch(req);
  };

  return (
    <div className="search-bar-container">
      {/* Mode toggle */}
      <div className="search-mode-toggle">
        <button
          type="button"
          className={`mode-btn${mode === "quick" ? " active" : ""}`}
          onClick={() => setMode("quick")}
        >
          {t("search.quick")}
        </button>
        <button
          type="button"
          className={`mode-btn${mode === "advanced" ? " active" : ""}`}
          onClick={() => setMode("advanced")}
        >
          {t("search.advanced")}
        </button>
      </div>

      {mode === "quick" ? (
        /* Quick Search (original) */
        <form onSubmit={handleQuickSubmit} className="search-bar">
          <div className="search-input-wrap">
            <svg className="search-icon" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z"
                clipRule="evenodd"
              />
            </svg>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleQuickKeyDown}
              placeholder="Object name or coordinates (e.g. M31, 10.68 41.27)"
              className="search-input"
            />
          </div>

          <div className="search-options">
            <div className="segmented-control">
              {ALL_SOURCES.map((s) => {
                const available = isSourceAvailable(s);
                return (
                  <button
                    key={s}
                    type="button"
                    className={
                      `segment-btn` +
                      (sources.includes(s) ? " active" : "") +
                      (available ? "" : " maintenance")
                    }
                    onClick={() => toggleSource(s)}
                    disabled={!available}
                    aria-disabled={!available}
                    title={
                      available
                        ? (SOURCE_TOOLTIPS[s] || s.toUpperCase())
                        : `${s.toUpperCase()} — under maintenance (provenance v2 rollout). Only ${V2_AVAILABLE_LABEL} are active.`
                    }
                  >
                    {s.toUpperCase()}{!available ? " · 维护中" : ""}
                  </button>
                );
              })}
            </div>

            <label className="radius-label">
              Radius (&deg;)
              <input
                type="number"
                value={radius}
                onChange={(e) => setRadius(Number(e.target.value))}
                min={0.001}
                max={10}
                step="any"
                className="radius-input"
              />
            </label>
          </div>

          {!query.trim() && (
            <div className="search-suggestions-hint">
              <span className="search-suggestions-label">{t("search.suggestions_heading")}</span>
              <div className="search-suggestions-list">
                <button type="button" className="search-suggestion-chip" onClick={() => handleSuggestionSelect("M31")}>
                  &quot;M31&quot; &mdash; {t("search.suggestion.m31")}
                </button>
                <button type="button" className="search-suggestion-chip" onClick={() => handleSuggestionSelect("Sirius")}>
                  &quot;Sirius&quot; &mdash; {t("search.suggestion.sirius")}
                </button>
                <button type="button" className="search-suggestion-chip" onClick={() => handleSuggestionSelect("Crab Nebula")}>
                  &quot;Crab Nebula&quot; &mdash; {t("search.suggestion.crab")}
                </button>
                <button type="button" className="search-suggestion-chip" onClick={() => handleSuggestionSelect("10.68 41.27")}>
                  &quot;10.68 41.27&quot; &mdash; {t("search.suggestion.coords")}
                </button>
              </div>
            </div>
          )}

          <button
            type="button"
            className="btn-search"
            disabled={loading || !query.trim()}
            onClick={() => submitQuickSearch()}
          >
            {loading ? <span className="spinner" /> : null}
            {loading ? t("search.searching") : t("search.search")}
          </button>
        </form>
      ) : (
        /* Advanced Search */
        <form onSubmit={handleAdvancedSubmit} className="search-bar advanced">
          {/* Natural language input */}
          <div className="search-input-wrap adv-input-wrap">
            <svg className="search-icon" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z"
                clipRule="evenodd"
              />
            </svg>
            <input
              type="text"
              value={naturalQuery}
              onChange={(e) => setNaturalQuery(e.target.value)}
              placeholder="e.g., high redshift [C II] line data, z > 6 Lyman-alpha emitters, X-ray AGN spectra..."
              className="search-input"
            />
          </div>

          {/* Collapsible filters */}
          <button
            type="button"
            className="btn-toggle-filters"
            onClick={() => setShowFilters(!showFilters)}
          >
            {showFilters ? "Hide Filters" : "Show Filters"}
            <svg
              className={`chevron${showFilters ? " open" : ""}`}
              viewBox="0 0 20 20"
              fill="currentColor"
              width="14"
              height="14"
            >
              <path
                fillRule="evenodd"
                d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>

          {showFilters && (
            <div className="adv-filters">
              {/* Row 1: Redshift + Spectral line */}
              <div className="filter-row">
                <div className="filter-group">
                  <label className="filter-label">Redshift Range</label>
                  <div className="filter-range">
                    <input
                      type="number"
                      value={redshiftMin}
                      onChange={(e) => setRedshiftMin(e.target.value)}
                      placeholder="z min"
                      step="any"
                      min="0"
                      className="filter-input"
                    />
                    <span className="range-sep">&ndash;</span>
                    <input
                      type="number"
                      value={redshiftMax}
                      onChange={(e) => setRedshiftMax(e.target.value)}
                      placeholder="z max"
                      step="any"
                      min="0"
                      className="filter-input"
                    />
                  </div>
                </div>

                <div className="filter-group">
                  <label className="filter-label">Spectral Line</label>
                  <select
                    value={spectralLine}
                    onChange={(e) => setSpectralLine(e.target.value)}
                    className="filter-select"
                  >
                    <option value="">Any</option>
                    {spectralLines.map((line) => (
                      <option key={line.key} value={line.key}>
                        {line.name} ({line.rest_wavelength_um.toFixed(2)} &mu;m)
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Row 2: Wavelength range + Object type */}
              <div className="filter-row">
                <div className="filter-group">
                  <label className="filter-label">
                    Wavelength Range (&mu;m)
                  </label>
                  <div className="filter-range">
                    <input
                      type="number"
                      value={wavelengthMin}
                      onChange={(e) => setWavelengthMin(e.target.value)}
                      placeholder="min"
                      step="any"
                      min="0"
                      className="filter-input"
                    />
                    <span className="range-sep">&ndash;</span>
                    <input
                      type="number"
                      value={wavelengthMax}
                      onChange={(e) => setWavelengthMax(e.target.value)}
                      placeholder="max"
                      step="any"
                      min="0"
                      className="filter-input"
                    />
                  </div>
                </div>

                <div className="filter-group">
                  <label className="filter-label">Object Type</label>
                  <select
                    value={objectType}
                    onChange={(e) => setObjectType(e.target.value)}
                    className="filter-select"
                  >
                    <option value="">Any</option>
                    {OBJECT_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Row 3: Observation type + Coordinates */}
              <div className="filter-row">
                <div className="filter-group">
                  <label className="filter-label">Observation Type</label>
                  <select
                    value={observationType}
                    onChange={(e) => setObservationType(e.target.value)}
                    className="filter-select"
                  >
                    <option value="">Any</option>
                    {OBSERVATION_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="filter-group">
                  <label className="filter-label">
                    Coordinates
                    <span
                      className="help-tooltip"
                      title="Right ascension and declination in decimal degrees. The search radius defines the angular search cone around these coordinates."
                    >
                      ?
                    </span>
                  </label>
                  <div className="filter-coords">
                    <input
                      type="number"
                      value={advRa}
                      onChange={(e) => setAdvRa(e.target.value)}
                      placeholder="RA (&deg;)"
                      step="any"
                      min="0"
                      max="360"
                      className="filter-input"
                    />
                    <input
                      type="number"
                      value={advDec}
                      onChange={(e) => setAdvDec(e.target.value)}
                      placeholder="Dec (&deg;)"
                      step="any"
                      min="-90"
                      max="90"
                      className="filter-input"
                    />
                    <label className="radius-label-small">
                      Radius (&deg;)
                      <span
                        className="help-tooltip"
                        title="Angular search radius in degrees. This defines how wide the cone search is around the given RA/Dec coordinates. 1 degree is about twice the Moon's diameter."
                      >
                        ?
                      </span>
                      <input
                        type="number"
                        value={advRadius}
                        onChange={(e) => setAdvRadius(Number(e.target.value))}
                        min={0.001}
                        max={10}
                        step="any"
                        className="filter-input"
                      />
                    </label>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Source selection */}
          <div className="search-options">
            <div className="segmented-control">
              {ALL_SOURCES.map((s) => {
                const available = isSourceAvailable(s);
                return (
                  <button
                    key={s}
                    type="button"
                    className={
                      `segment-btn` +
                      (advSources.includes(s) ? " active" : "") +
                      (available ? "" : " maintenance")
                    }
                    onClick={() => toggleAdvSource(s)}
                    disabled={!available}
                    aria-disabled={!available}
                    title={
                      available
                        ? (SOURCE_TOOLTIPS[s] || s.toUpperCase())
                        : `${s.toUpperCase()} — under maintenance (provenance v2 rollout). Only ${V2_AVAILABLE_LABEL} are active.`
                    }
                  >
                    {s.toUpperCase()}{!available ? " · 维护中" : ""}
                  </button>
                );
              })}
            </div>
          </div>

          <button
            type="submit"
            className="btn-search"
            disabled={loading || advSources.length === 0}
          >
            {loading ? <span className="spinner" /> : null}
            {loading ? t("search.searching") : t("search.advanced")}
          </button>
        </form>
      )}
    </div>
  );
}
