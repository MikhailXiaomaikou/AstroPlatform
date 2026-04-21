import { useCallback, useEffect, useRef, useState } from "react";
import {
  getFITSHeader,
  getFITSSpectrum,
  getFITSWCS,
  analyzeSpectrum,
  getStoredApiKey,
  type FITSHeader,
  type FITSSpectrum,
  type WCSGridData,
  type SpectrumAnalysis,
} from "../../api/client";
import MarkdownText from "../chat/MarkdownText";

interface Props {
  filename: string;
  fitsPath: string;
  source: string;
  objectId: string;
}

type Tab = "info" | "header" | "data" | "image" | "analysis";

// Common emission/absorption lines for annotation
const SPECTRAL_LINES: Record<string, number> = {
  "Ly-α": 1216,
  "OII": 3727,
  "CaK": 3934,
  "CaH": 3969,
  "H-β": 4861,
  "OIII": 5007,
  "NII": 6548,
  "H-α": 6563,
  "SII": 6717,
};

type StretchMode = "linear" | "log" | "sqrt" | "asinh" | "power";
type ColormapName = "grayscale" | "heat" | "cool" | "viridis" | "plasma";

function applyStretch(value: number, min: number, max: number, mode: StretchMode): number {
  const range = max - min || 1;
  let norm = Math.max(0, Math.min(1, (value - min) / range));
  switch (mode) {
    case "log":
      norm = Math.log1p(norm * 1000) / Math.log1p(1000);
      break;
    case "sqrt":
      norm = Math.sqrt(norm);
      break;
    case "asinh":
      norm = Math.asinh(norm * 10) / Math.asinh(10);
      break;
    case "power":
      norm = Math.pow(norm, 2);
      break;
  }
  return norm;
}

function applyColormap(norm: number, cmap: ColormapName): [number, number, number] {
  const v = Math.max(0, Math.min(1, norm));
  const b = Math.round(v * 255);
  switch (cmap) {
    case "heat":
      return [Math.min(255, b * 3), Math.max(0, b * 3 - 255), Math.max(0, b * 3 - 510)];
    case "cool":
      return [Math.max(0, b * 2 - 255), b, Math.min(255, b * 2)];
    case "viridis": {
      const r = Math.round(68 + v * (253 - 68));
      const g = Math.round(1 + v * (231 - 1));
      const bl = Math.round(84 + v * (37 - 84));
      return [r, g, bl];
    }
    case "plasma": {
      const r = Math.round(13 + v * (240 - 13));
      const g = Math.round(8 + v * (249 - 8) * (1 - v));
      const bl = Math.round(135 + v * (33 - 135));
      return [r, g, bl];
    }
    default:
      return [b, b, b];
  }
}

export default function FITSPreview({ filename, fitsPath, source, objectId }: Props) {
  const [tab, setTab] = useState<Tab>("info");
  const [header, setHeader] = useState<FITSHeader | null>(null);
  const [spectrum, setSpectrum] = useState<FITSSpectrum | null>(null);
  const [headerLoading, setHeaderLoading] = useState(false);
  const [spectrumLoading, setSpectrumLoading] = useState(false);
  const [headerFilter, setHeaderFilter] = useState("");
  const [selectedHdu, setSelectedHdu] = useState(0);
  const [analysis, setAnalysis] = useState<SpectrumAnalysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  // Fetch header on mount so HDU selector is available on all tabs
  useEffect(() => {
    if (!header && !headerLoading) {
      setHeaderLoading(true);
      getFITSHeader(fitsPath, selectedHdu)
        .then(setHeader)
        .catch(() => {})
        .finally(() => setHeaderLoading(false));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitsPath]);

  useEffect(() => {
    if ((tab === "data" || tab === "image") && !spectrum && !spectrumLoading) {
      setSpectrumLoading(true);
      getFITSSpectrum(fitsPath)
        .then(setSpectrum)
        .catch(() => {})
        .finally(() => setSpectrumLoading(false));
    }
  }, [tab, fitsPath, spectrum, spectrumLoading]);

  const handleHduChange = (hduIdx: number) => {
    setSelectedHdu(hduIdx);
    setHeaderLoading(true);
    getFITSHeader(fitsPath, hduIdx)
      .then(setHeader)
      .catch(() => {})
      .finally(() => setHeaderLoading(false));
  };

  const tabs: Tab[] = ["info", "header", "data"];
  if (spectrum?.type === "image" || spectrum?.thumbnail) tabs.push("image");
  tabs.push("analysis");

  const handleAnalyze = () => {
    setAnalysisLoading(true);
    setAnalysisError(null);
    const apiKey = getStoredApiKey("anthropic") || undefined;
    analyzeSpectrum(fitsPath, apiKey)
      .then((result) => {
        setAnalysis(result);
        setTab("analysis");
      })
      .catch((e) => {
        if (e && typeof e === "object" && "response" in e) {
          const resp = (e as { response?: { data?: { detail?: string } } }).response;
          setAnalysisError(resp?.data?.detail || "Analysis failed");
        } else {
          setAnalysisError(e instanceof Error ? e.message : "Analysis failed");
        }
      })
      .finally(() => setAnalysisLoading(false));
  };

  return (
    <div className="fits-preview">
      <div className="fits-preview-header">
        <h3>FITS File</h3>
        {header && header.hdus.length > 1 && (
          <select
            value={selectedHdu}
            onChange={(e) => handleHduChange(Number(e.target.value))}
            style={{
              background: "var(--color-bg-secondary, #2a2a2e)",
              color: "var(--color-text, #e0e0e0)",
              border: "1px solid var(--color-border, #555)",
              borderRadius: 4,
              padding: "0.2rem 0.5rem",
              fontSize: "0.78rem",
            }}
            title="Select HDU (Header Data Unit)"
          >
            {header.hdus.map((hdu) => (
              <option key={hdu.index} value={hdu.index}>
                HDU {hdu.index}: {hdu.name} ({hdu.type})
              </option>
            ))}
          </select>
        )}
        <a
          href={`${import.meta.env.VITE_API_URL || (import.meta.env.DEV || import.meta.env.MODE === "test" ? "http://localhost:8000" : "https://astro-backend-h4x1.onrender.com")}/api/data/fits/download?fits_path=${encodeURIComponent(fitsPath)}`}
          download
          className="btn-secondary btn-small"
          style={{ marginLeft: "auto", textDecoration: "none", fontSize: "0.72rem" }}
        >
          Download
        </a>
        <div className="fits-tabs">
          {tabs.map((t) => (
            <button
              key={t}
              className={`fits-tab${tab === t ? " active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t === "info" ? "Info" : t === "header" ? "Header" : t === "image" ? "Image" : "Data Preview"}
            </button>
          ))}
        </div>
      </div>

      {tab === "info" && (
        <div className="fits-info-tab">
          <dl className="fits-meta">
            <dt>File</dt><dd>{filename}</dd>
            <dt>Source</dt>
            <dd><span className={`badge badge-${source}`}>{source.toUpperCase()}</span></dd>
            <dt>Object ID</dt><dd>{objectId}</dd>
            <dt>Storage Path</dt><dd className="mono">{fitsPath}</dd>
          </dl>
          {header && (
            <div className="fits-hdu-summary">
              <h4>HDUs ({header.hdus.length})</h4>
              {header.hdus.map((hdu) => (
                <div key={hdu.index} className="hdu-card">
                  <span className="hdu-name">{hdu.name}</span>
                  <span className="hdu-type">{hdu.type}</span>
                  {hdu.shape && <span className="hdu-shape">{hdu.shape.join(" x ")}</span>}
                  {hdu.columns && <span className="hdu-cols">{hdu.columns.length} columns</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "header" && (
        <div className="fits-header-tab">
          {headerLoading ? (
            <div className="fits-loading"><span className="spinner spinner-blue" /> Loading headers...</div>
          ) : header ? (
            <>
              {header.hdus.length > 1 && (
                <div className="hdu-selector">
                  {header.hdus.map((hdu) => (
                    <button
                      key={hdu.index}
                      className={`hdu-btn${selectedHdu === hdu.index ? " active" : ""}`}
                      onClick={() => setSelectedHdu(hdu.index)}
                    >{hdu.name}</button>
                  ))}
                </div>
              )}
              <input
                type="text" className="header-filter" placeholder="Filter headers..."
                value={headerFilter} onChange={(e) => setHeaderFilter(e.target.value)}
              />
              <div className="header-table-wrap">
                <table className="header-table">
                  <thead><tr><th>Key</th><th>Value</th><th>Comment</th></tr></thead>
                  <tbody>
                    {header.headers
                      .find((h) => h.hdu_index === selectedHdu)
                      ?.cards.filter((c) =>
                        !headerFilter ||
                        c.key.toLowerCase().includes(headerFilter.toLowerCase()) ||
                        c.value.toLowerCase().includes(headerFilter.toLowerCase())
                      )
                      .map((card, i) => (
                        <tr key={i}>
                          <td className="mono header-key">{card.key}</td>
                          <td className="mono">{card.value}</td>
                          <td className="header-comment">{card.comment}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p className="fits-hint">Failed to load headers</p>
          )}
        </div>
      )}

      {tab === "data" && (
        <div className="fits-data-tab">
          {spectrumLoading ? (
            <div className="fits-loading"><span className="spinner spinner-blue" /> Loading data...</div>
          ) : spectrum ? (
            <SpectrumView spectrum={spectrum} analysisLines={analysis?.ai_lines as AnalysisLine[] | undefined} />
          ) : (
            <p className="fits-hint">No data available</p>
          )}
        </div>
      )}

      {tab === "image" && (
        <div className="fits-data-tab">
          {spectrumLoading ? (
            <div className="fits-loading"><span className="spinner spinner-blue" /> Loading image...</div>
          ) : spectrum?.type === "image" ? (
            <ImageViewer spectrum={spectrum} fitsPath={fitsPath} />
          ) : (
            <p className="fits-hint">No image data available</p>
          )}
        </div>
      )}

      {tab === "analysis" && (
        <div className="fits-data-tab fits-analysis-tab">
          {!analysis && !analysisLoading && !analysisError && (
            <div className="fits-analysis-prompt">
              <p>AI will analyze this spectrum: detect peaks, estimate redshift, classify the object, and suggest next steps.</p>
              <button className="btn-primary" onClick={handleAnalyze}>
                Analyze with AI
              </button>
            </div>
          )}
          {analysisLoading && (
            <div className="fits-loading"><span className="spinner spinner-blue" /> Analyzing spectrum (this may take 10-15 seconds)...</div>
          )}
          {analysisError && (
            <div className="error-banner" style={{ margin: "1rem 0" }}>
              {analysisError}
              <button className="btn-secondary btn-small" style={{ marginLeft: 8 }} onClick={handleAnalyze}>Retry</button>
            </div>
          )}
          {analysis && (
            <div className="fits-analysis-result">
              {/* Classification header */}
              <div className="analysis-header">
                {analysis.ai_classification && (
                  <span className="analysis-class-badge">{analysis.ai_classification}</span>
                )}
                {analysis.ai_confidence && (
                  <span className={`analysis-confidence confidence-${analysis.ai_confidence}`}>
                    {analysis.ai_confidence} confidence
                  </span>
                )}
                {(analysis.ai_redshift?.value != null) && (
                  <span className="analysis-z">z = {analysis.ai_redshift.value.toFixed(6)}</span>
                )}
                {(!analysis.ai_classification && analysis.redshift_auto) && (
                  <span className="analysis-z">z(auto) = {analysis.redshift_auto.best_z.toFixed(6)}</span>
                )}
              </div>

              {/* AI Summary */}
              {analysis.ai_summary && (
                <div className="analysis-summary">{analysis.ai_summary}</div>
              )}

              {/* AI Narrative */}
              {analysis.ai_narrative && (
                <div className="analysis-narrative">
                  <MarkdownText content={analysis.ai_narrative} />
                </div>
              )}

              {/* Identified lines table */}
              {analysis.ai_lines && analysis.ai_lines.length > 0 && (
                <div className="analysis-section">
                  <h4>Identified Spectral Lines</h4>
                  <table className="analysis-lines-table">
                    <thead>
                      <tr>
                        <th>Line</th>
                        <th>Rest (\u00C5)</th>
                        <th>Observed (\u00C5)</th>
                        <th>Type</th>
                        <th>Strength</th>
                      </tr>
                    </thead>
                    <tbody>
                      {analysis.ai_lines.map((line, i) => (
                        <tr key={i}>
                          <td>{line.name}</td>
                          <td>{line.rest_wavelength?.toFixed(1)}</td>
                          <td>{line.observed_wavelength?.toFixed(1)}</td>
                          <td className={line.type === "emission" ? "line-emission" : "line-absorption"}>
                            {line.type}
                          </td>
                          <td>{line.strength}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Detected peaks */}
              {analysis.peaks && analysis.peaks.length > 0 && (
                <div className="analysis-section">
                  <h4>Detected Peaks ({analysis.peaks.length})</h4>
                  <div className="analysis-peaks">
                    {analysis.peaks.slice(0, 15).map((p, i) => (
                      <span key={i} className={`analysis-peak ${p.is_emission ? "peak-em" : "peak-abs"}`}>
                        {p.wavelength.toFixed(1)} \u00C5 <small>SNR {p.snr.toFixed(1)}</small>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Special features */}
              {analysis.ai_special_features && analysis.ai_special_features.length > 0 && (
                <div className="analysis-section">
                  <h4>Special Features</h4>
                  <ul className="analysis-features">
                    {analysis.ai_special_features.map((f, i) => <li key={i}>{f}</li>)}
                  </ul>
                </div>
              )}

              {/* Next steps */}
              {analysis.ai_next_steps && analysis.ai_next_steps.length > 0 && (
                <div className="analysis-section">
                  <h4>Suggested Next Steps</h4>
                  <ol className="analysis-steps">
                    {analysis.ai_next_steps.map((s, i) => <li key={i}>{s}</li>)}
                  </ol>
                </div>
              )}

              <button className="btn-secondary btn-small" style={{ marginTop: "1rem" }} onClick={handleAnalyze}>
                Re-analyze
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════
// Interactive Spectrum Viewer
// ════════════════════════════════════════════

interface AnalysisLine {
  name: string;
  observed_wavelength: number;
  type: string;
  strength: string;
}

function SpectrumView({ spectrum, analysisLines }: { spectrum: FITSSpectrum; analysisLines?: AnalysisLine[] }) {
  const [zoomRange, setZoomRange] = useState<[number, number] | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState(0);
  const [showLines, setShowLines] = useState(false);
  const [cursorPos, setCursorPos] = useState<{ x: number; y: number; dataX: number; dataY: number } | null>(null);
  const [continuumMode, setContinuumMode] = useState(false);
  const [continuumPoints, setContinuumPoints] = useState<number[]>([]);
  const [selectedColumn, setSelectedColumn] = useState<string>("");

  if (spectrum.type === "empty") {
    return <p className="fits-hint">No data in this FITS file</p>;
  }

  if (spectrum.type === "image") {
    return <ImageViewer spectrum={spectrum} />;
  }

  const columns = spectrum.columns;
  const xKey = columns.includes("wavelength") ? "wavelength"
    : columns.includes("loglam") ? "loglam"
    : columns.includes("index") ? "index" : columns[0];

  const defaultYKey = columns.includes("flux") ? "flux"
    : columns.length > 1 ? columns[1] : columns[0];
  const yKey = selectedColumn || defaultYKey;

  const xData = spectrum.data[xKey] || [];
  const yData = spectrum.data[yKey] || [];

  if (xData.length === 0) return <p className="fits-hint">No plottable data</p>;

  const startIdx = zoomRange ? zoomRange[0] : 0;
  const endIdx = zoomRange ? zoomRange[1] : xData.length;
  const xSlice = xData.slice(startIdx, endIdx);
  const ySlice = yData.slice(startIdx, endIdx);

  const finiteY = ySlice.filter((v) => v != null && isFinite(v));
  const xMin = Math.min(...xSlice.filter((v) => v != null));
  const xMax = Math.max(...xSlice.filter((v) => v != null));
  const yMin = Math.min(...finiteY);
  const yMax = Math.max(...finiteY);

  const W = 900;
  const H = 340;
  const pad = { top: 20, right: 20, bottom: 40, left: 70 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  const xScale = (v: number) => pad.left + ((v - xMin) / (xMax - xMin || 1)) * plotW;
  const yScale = (v: number) => pad.top + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH;
  const xInverse = (px: number) => xMin + ((px - pad.left) / plotW) * (xMax - xMin);
  const yInverse = (py: number) => yMax - ((py - pad.top) / plotH) * (yMax - yMin);

  const pathD = xSlice
    .map((x, i) => {
      if (x == null || ySlice[i] == null) return "";
      return `${i === 0 || xSlice[i - 1] == null ? "M" : "L"}${xScale(x)},${yScale(ySlice[i])}`;
    })
    .join(" ");

  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (continuumMode) return;
    const rect = e.currentTarget.getBoundingClientRect();
    setIsDragging(true);
    setDragStart(e.clientX - rect.left);
  };

  const handleMouseUp = (e: React.MouseEvent<SVGSVGElement>) => {
    if (continuumMode) {
      const rect = e.currentTarget.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const wl = xInverse(px);
      setContinuumPoints((prev) => [...prev, wl]);
      return;
    }
    if (!isDragging) return;
    setIsDragging(false);
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const x1 = Math.min(dragStart, x);
    const x2 = Math.max(dragStart, x);
    if (x2 - x1 < 10) { setZoomRange(null); return; }
    const frac1 = Math.max(0, (x1 - pad.left) / plotW);
    const frac2 = Math.min(1, (x2 - pad.left) / plotW);
    const totalLen = zoomRange ? zoomRange[1] - zoomRange[0] : xData.length;
    const base = zoomRange ? zoomRange[0] : 0;
    const newStart = Math.floor(base + frac1 * totalLen);
    const newEnd = Math.ceil(base + frac2 * totalLen);
    if (newEnd - newStart > 2) setZoomRange([newStart, newEnd]);
  };

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (px >= pad.left && px <= W - pad.right && py >= pad.top && py <= H - pad.bottom) {
      setCursorPos({ x: px, y: py, dataX: xInverse(px), dataY: yInverse(py) });
    } else {
      setCursorPos(null);
    }
  };

  const yOtherColumns = columns.filter((c) => c !== xKey);

  return (
    <div className="fits-spectrum-view">
      <div className="spectrum-controls">
        <span className="spectrum-axes">X: {xKey} | Y: {yKey} | Points: {xSlice.length}</span>
        {yOtherColumns.length > 1 && (
          <select
            className="column-select"
            value={yKey}
            onChange={(e) => setSelectedColumn(e.target.value)}
          >
            {yOtherColumns.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        )}
        <label className="spectrum-checkbox">
          <input type="checkbox" checked={showLines} onChange={(e) => setShowLines(e.target.checked)} />
          Lines
        </label>
        <label className="spectrum-checkbox">
          <input type="checkbox" checked={continuumMode} onChange={(e) => {
            setContinuumMode(e.target.checked);
            if (!e.target.checked) setContinuumPoints([]);
          }} />
          Continuum
        </label>
        {zoomRange && (
          <button className="btn-secondary" onClick={() => setZoomRange(null)}>Reset Zoom</button>
        )}
        <span className="spectrum-hint">Drag to zoom, click to reset</span>
      </div>

      {cursorPos && (
        <div className="cursor-readout">
          {xKey}: {cursorPos.dataX.toPrecision(6)} | {yKey}: {cursorPos.dataY.toPrecision(6)}
        </div>
      )}

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="spectrum-svg"
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setCursorPos(null)}
      >
        {/* Grid */}
        {Array.from({ length: 5 }).map((_, i) => {
          const y = pad.top + (plotH / 4) * i;
          return <line key={`gy${i}`} x1={pad.left} y1={y} x2={W - pad.right} y2={y} stroke="rgba(255,255,255,0.06)" />;
        })}

        {/* Spectral line annotations */}
        {showLines && Object.entries(SPECTRAL_LINES).map(([name, wl]) => {
          if (wl < xMin || wl > xMax) return null;
          const x = xScale(wl);
          return (
            <g key={name}>
              <line x1={x} y1={pad.top} x2={x} y2={H - pad.bottom} stroke="rgba(255,69,58,0.4)" strokeDasharray="3 3" />
              <text x={x + 2} y={pad.top + 10} fill="rgba(255,69,58,0.7)" fontSize={8} transform={`rotate(-90, ${x + 2}, ${pad.top + 10})`}>
                {name} {wl}
              </text>
            </g>
          );
        })}

        {/* AI-identified lines */}
        {analysisLines && analysisLines.map((ln) => {
          const wl = ln.observed_wavelength;
          if (!wl || wl < xMin || wl > xMax) return null;
          const x = xScale(wl);
          const color = ln.type === "emission" ? "rgba(48,209,88,0.7)" : "rgba(100,210,255,0.7)";
          return (
            <g key={`ai-${ln.name}-${wl}`}>
              <line x1={x} y1={pad.top} x2={x} y2={H - pad.bottom} stroke={color} strokeWidth={1.5} />
              <text x={x + 3} y={pad.top + 12} fill={color} fontSize={9} fontWeight={600}
                transform={`rotate(-90, ${x + 3}, ${pad.top + 12})`}>
                {ln.name}
              </text>
            </g>
          );
        })}

        {/* Continuum points */}
        {continuumPoints.map((wl, i) => {
          const x = xScale(wl);
          return <line key={`cp${i}`} x1={x} y1={pad.top} x2={x} y2={H - pad.bottom} stroke="rgba(48,209,88,0.5)" strokeDasharray="4 2" />;
        })}

        {/* Axes labels */}
        <text x={W / 2} y={H - 5} textAnchor="middle" fill="rgba(255,255,255,0.4)" fontSize={11}>{xKey}</text>
        <text x={12} y={H / 2} textAnchor="middle" fill="rgba(255,255,255,0.4)" fontSize={11} transform={`rotate(-90, 12, ${H / 2})`}>{yKey}</text>

        {/* Y axis ticks */}
        {Array.from({ length: 5 }).map((_, i) => {
          const val = yMin + ((yMax - yMin) / 4) * (4 - i);
          const y = pad.top + (plotH / 4) * i;
          return <text key={`yt${i}`} x={pad.left - 5} y={y + 4} textAnchor="end" fill="rgba(255,255,255,0.3)" fontSize={9}>{val.toPrecision(3)}</text>;
        })}
        {/* X axis ticks */}
        {Array.from({ length: 5 }).map((_, i) => {
          const val = xMin + ((xMax - xMin) / 4) * i;
          const x = pad.left + (plotW / 4) * i;
          return <text key={`xt${i}`} x={x} y={H - pad.bottom + 15} textAnchor="middle" fill="rgba(255,255,255,0.3)" fontSize={9}>{val.toPrecision(4)}</text>;
        })}

        {/* Crosshair cursor */}
        {cursorPos && (
          <>
            <line x1={cursorPos.x} y1={pad.top} x2={cursorPos.x} y2={H - pad.bottom} stroke="rgba(255,255,255,0.15)" />
            <line x1={pad.left} y1={cursorPos.y} x2={W - pad.right} y2={cursorPos.y} stroke="rgba(255,255,255,0.15)" />
          </>
        )}

        {/* Data line */}
        <path d={pathD} fill="none" stroke="#38bdf8" strokeWidth={1.2} />
      </svg>
    </div>
  );
}

// ════════════════════════════════════════════
// DS9-like Image Viewer
// ════════════════════════════════════════════

function ImageViewer({ spectrum, fitsPath }: { spectrum: FITSSpectrum; fitsPath?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [stretch, setStretch] = useState<StretchMode>("linear");
  const [colormap, setColormap] = useState<ColormapName>("grayscale");
  const [showContours, setShowContours] = useState(false);
  const [cutLow, setCutLow] = useState(spectrum.min ?? 0);
  const [cutHigh, setCutHigh] = useState(spectrum.max ?? 1);
  const [pixelInfo, setPixelInfo] = useState<string>("");
  const [showWCS, setShowWCS] = useState(false);
  const [wcsData, setWcsData] = useState<WCSGridData | null>(null);
  const [wcsLoading, setWcsLoading] = useState(false);
  const [blinkFrames, setBlinkFrames] = useState<number[][][]>([]);
  const [blinkActive, setBlinkActive] = useState(false);
  const [blinkIdx, setBlinkIdx] = useState(0);
  const [blinkSpeed, setBlinkSpeed] = useState(500);
  const blinkTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Region selection for photometry
  const [regionMode, setRegionMode] = useState(false);
  const [regionStart, setRegionStart] = useState<{ x: number; y: number } | null>(null);
  const [regionEnd, setRegionEnd] = useState<{ x: number; y: number } | null>(null);
  const [regionStats, setRegionStats] = useState<{ min: number; max: number; mean: number; std: number; sum: number; count: number } | null>(null);

  const data = spectrum.thumbnail;
  const imgMin = spectrum.min ?? 0;
  const imgMax = spectrum.max ?? 1;

  const renderImage = useCallback(() => {
    if (!canvasRef.current || !data) return;
    const h = data.length;
    const w = data[0].length;
    const canvas = canvasRef.current;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const imageData = ctx.createImageData(w, h);

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const norm = applyStretch(data[y][x], cutLow, cutHigh, stretch);
        const [r, g, b] = applyColormap(norm, colormap);
        const idx = (y * w + x) * 4;
        imageData.data[idx] = r;
        imageData.data[idx + 1] = g;
        imageData.data[idx + 2] = b;
        imageData.data[idx + 3] = 255;
      }
    }
    ctx.putImageData(imageData, 0, 0);

    // Draw contours
    if (showContours && data) {
      const levels = 5;
      ctx.strokeStyle = "rgba(255,255,255,0.3)";
      ctx.lineWidth = 0.5;
      for (let l = 1; l <= levels; l++) {
        const threshold = cutLow + (l / (levels + 1)) * (cutHigh - cutLow);
        for (let y = 1; y < h - 1; y++) {
          for (let x = 1; x < w - 1; x++) {
            const val = data[y][x];
            if (
              (val >= threshold && data[y - 1][x] < threshold) ||
              (val >= threshold && data[y][x - 1] < threshold)
            ) {
              ctx.fillStyle = "rgba(255,255,255,0.4)";
              ctx.fillRect(x, y, 1, 1);
            }
          }
        }
      }
    }
  }, [data, stretch, colormap, cutLow, cutHigh, showContours]);

  useEffect(() => {
    renderImage();
  }, [renderImage]);

  // Load WCS grid when toggled on
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    // Q3: toggle-triggered async fetch with loading / data state sync.
    if (showWCS && !wcsData && !wcsLoading && fitsPath) {
      setWcsLoading(true);
      getFITSWCS(fitsPath)
        .then(setWcsData)
        .catch(() => {})
        .finally(() => setWcsLoading(false));
    }
  }, [showWCS, wcsData, wcsLoading, fitsPath]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Draw WCS grid overlay on canvas after image renders
  useEffect(() => {
    if (!showWCS || !wcsData?.has_wcs || !canvasRef.current || !data) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const w = data[0].length;
    const h = data.length;

    ctx.strokeStyle = "rgba(0, 255, 128, 0.35)";
    ctx.lineWidth = 0.5;
    for (const line of wcsData.grid_lines) {
      if (line.points.length < 2) continue;
      ctx.beginPath();
      ctx.moveTo(line.points[0][0] * w, line.points[0][1] * h);
      for (let i = 1; i < line.points.length; i++) {
        ctx.lineTo(line.points[i][0] * w, line.points[i][1] * h);
      }
      ctx.stroke();
    }

    ctx.fillStyle = "rgba(0, 255, 128, 0.6)";
    ctx.font = "8px monospace";
    for (const label of wcsData.labels) {
      ctx.fillText(label.text, label.x * w, label.y * h);
    }
  }, [showWCS, wcsData, data, renderImage]);

  // Blink animation timer
  useEffect(() => {
    if (blinkActive && blinkFrames.length > 1) {
      blinkTimerRef.current = setInterval(() => {
        setBlinkIdx((prev) => (prev + 1) % blinkFrames.length);
      }, blinkSpeed);
    }
    return () => {
      if (blinkTimerRef.current) clearInterval(blinkTimerRef.current);
    };
  }, [blinkActive, blinkFrames.length, blinkSpeed]);

  // Render blink frame when blinkIdx changes
  useEffect(() => {
    if (!blinkActive || blinkFrames.length === 0 || !canvasRef.current) return;
    const frame = blinkFrames[blinkIdx];
    const h = frame.length;
    const w = frame[0].length;
    const canvas = canvasRef.current;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const imageData = ctx.createImageData(w, h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const norm = applyStretch(frame[y][x], cutLow, cutHigh, stretch);
        const [r, g, b] = applyColormap(norm, colormap);
        const idx = (y * w + x) * 4;
        imageData.data[idx] = r;
        imageData.data[idx + 1] = g;
        imageData.data[idx + 2] = b;
        imageData.data[idx + 3] = 255;
      }
    }
    ctx.putImageData(imageData, 0, 0);
  }, [blinkIdx, blinkActive, blinkFrames, cutLow, cutHigh, stretch, colormap]);

  // Add current image as a blink frame
  const addBlinkFrame = () => {
    if (data) {
      setBlinkFrames((prev) => [...prev, data]);
    }
  };

  const getPixelCoords = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current || !data) return null;
    const rect = canvasRef.current.getBoundingClientRect();
    const x = Math.floor((e.clientX - rect.left) * data[0].length / rect.width);
    const y = Math.floor((e.clientY - rect.top) * data.length / rect.height);
    if (x >= 0 && x < data[0].length && y >= 0 && y < data.length) return { x, y };
    return null;
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const p = getPixelCoords(e);
    if (p && data) {
      setPixelInfo(`Pixel (${p.x}, ${p.y}) = ${data[p.y][p.x].toFixed(4)}`);
      if (regionMode && regionStart) setRegionEnd(p);
    }
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!regionMode) return;
    const p = getPixelCoords(e);
    if (p) { setRegionStart(p); setRegionEnd(null); setRegionStats(null); }
  };

  const handleMouseUp = () => {
    if (!regionMode || !regionStart || !regionEnd || !data) return;
    const x1 = Math.min(regionStart.x, regionEnd.x);
    const x2 = Math.max(regionStart.x, regionEnd.x);
    const y1 = Math.min(regionStart.y, regionEnd.y);
    const y2 = Math.max(regionStart.y, regionEnd.y);
    if (x1 === x2 || y1 === y2) return;

    const vals: number[] = [];
    for (let y = y1; y <= y2; y++) {
      for (let x = x1; x <= x2; x++) {
        vals.push(data[y][x]);
      }
    }
    const sum = vals.reduce((a, b) => a + b, 0);
    const mean = sum / vals.length;
    const std = Math.sqrt(vals.reduce((a, v) => a + (v - mean) ** 2, 0) / vals.length);
    setRegionStats({
      min: Math.min(...vals),
      max: Math.max(...vals),
      mean,
      std,
      sum,
      count: vals.length,
    });
  };

  return (
    <div className="fits-image-viewer">
      <div className="image-controls">
        <div className="control-group">
          <label>Stretch</label>
          <select value={stretch} onChange={(e) => setStretch(e.target.value as StretchMode)} className="image-select">
            <option value="linear">Linear</option>
            <option value="log">Log</option>
            <option value="sqrt">Sqrt</option>
            <option value="asinh">Asinh</option>
            <option value="power">Power</option>
          </select>
        </div>
        <div className="control-group">
          <label>Colormap</label>
          <select value={colormap} onChange={(e) => setColormap(e.target.value as ColormapName)} className="image-select">
            <option value="grayscale">Grayscale</option>
            <option value="heat">Heat</option>
            <option value="cool">Cool</option>
            <option value="viridis">Viridis</option>
            <option value="plasma">Plasma</option>
          </select>
        </div>
        <div className="control-group">
          <label>Low Cut</label>
          <input
            type="range" min={imgMin} max={imgMax} step={(imgMax - imgMin) / 100}
            value={cutLow} onChange={(e) => setCutLow(Number(e.target.value))}
            className="image-slider"
          />
          <span className="cut-value">{cutLow.toPrecision(4)}</span>
        </div>
        <div className="control-group">
          <label>High Cut</label>
          <input
            type="range" min={imgMin} max={imgMax} step={(imgMax - imgMin) / 100}
            value={cutHigh} onChange={(e) => setCutHigh(Number(e.target.value))}
            className="image-slider"
          />
          <span className="cut-value">{cutHigh.toPrecision(4)}</span>
        </div>
        <label className="spectrum-checkbox">
          <input type="checkbox" checked={showContours} onChange={(e) => setShowContours(e.target.checked)} />
          Contours
        </label>
        {fitsPath && (
          <label className="spectrum-checkbox">
            <input type="checkbox" checked={showWCS} onChange={(e) => setShowWCS(e.target.checked)} />
            WCS Grid {wcsLoading ? "(loading...)" : ""}
          </label>
        )}
        <label className="spectrum-checkbox">
          <input type="checkbox" checked={regionMode} onChange={(e) => { setRegionMode(e.target.checked); setRegionStats(null); }} />
          Region Select
        </label>
      </div>

      {/* Blink Comparison Controls */}
      <div className="blink-controls">
        <button className="btn-secondary btn-small" onClick={addBlinkFrame}>
          Add Blink Frame ({blinkFrames.length})
        </button>
        {blinkFrames.length > 1 && (
          <>
            <button
              className={`btn-secondary btn-small${blinkActive ? " active" : ""}`}
              onClick={() => setBlinkActive(!blinkActive)}
            >
              {blinkActive ? "Stop Blink" : "Start Blink"}
            </button>
            <label className="blink-speed-label">
              Speed
              <input
                type="range" min={100} max={2000} step={100}
                value={blinkSpeed}
                onChange={(e) => setBlinkSpeed(Number(e.target.value))}
                className="image-slider"
              />
              <span className="cut-value">{blinkSpeed}ms</span>
            </label>
            <button
              className="btn-secondary btn-small"
              onClick={() => { setBlinkFrames([]); setBlinkActive(false); setBlinkIdx(0); }}
            >
              Clear Frames
            </button>
          </>
        )}
      </div>

      <div className="image-stats">
        <span>Shape: {spectrum.shape?.join(" x ")}</span>
        <span>Min: {spectrum.min?.toFixed(2)}</span>
        <span>Max: {spectrum.max?.toFixed(2)}</span>
        <span>Mean: {spectrum.mean?.toFixed(2)}</span>
      </div>

      {pixelInfo && <div className="pixel-readout">{pixelInfo}</div>}

      <canvas
        ref={canvasRef}
        className={`fits-image-canvas${regionMode ? " region-cursor" : ""}`}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => setPixelInfo("")}
      />

      {regionStats && (
        <div className="region-stats">
          <strong>Region Statistics</strong> ({regionStats.count} pixels)
          <div className="region-stats-grid">
            <span>Min: {regionStats.min.toFixed(4)}</span>
            <span>Max: {regionStats.max.toFixed(4)}</span>
            <span>Mean: {regionStats.mean.toFixed(4)}</span>
            <span>Std: {regionStats.std.toFixed(4)}</span>
            <span>Sum: {regionStats.sum.toFixed(2)}</span>
          </div>
        </div>
      )}
    </div>
  );
}
