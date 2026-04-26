import { useEffect, useId, useRef, useState, useCallback, type MouseEvent as ReactMouseEvent } from "react";

/* ── Aladin Lite type declarations ── */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AladinInstance = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AladinAPI = any;

declare global {
  interface Window {
    A?: AladinAPI;
  }
}

/* ── Survey presets ── */

const SURVEYS = [
  { id: "P/DSS2/color", label: "DSS2 color" },
  { id: "P/2MASS/J", label: "2MASS J" },
  { id: "P/allWISE/W1", label: "WISE W1" },
  { id: "P/Gaia/DR3/I", label: "Gaia DR3" },
  { id: "P/SDSS9/color", label: "SDSS DR9" },
];

/* ── Source color mapping ── */

const SOURCE_COLORS: Record<string, string> = {
  simbad: "#3b82f6",   // blue
  gaia: "#22c55e",     // green
  sdss: "#ef4444",     // red
  mast: "#f59e0b",     // amber
  vizier: "#a855f7",   // purple
  ned: "#06b6d4",      // cyan
  "2mass": "#f97316",  // orange
  chandra: "#ec4899",  // pink
  alma: "#14b8a6",     // teal
  allwise: "#eab308",  // yellow
  eso: "#8b5cf6",      // violet
  irsa: "#d946ef",     // fuchsia
  jwst: "#0ea5e9",     // sky
  lamost: "#84cc16",   // lime
};

function getSourceColor(source?: string): string {
  if (!source) return "#0A84FF";
  return SOURCE_COLORS[source.toLowerCase()] ?? "#0A84FF";
}

/* ── Props ── */

export interface AladinMarker {
  ra: number;
  dec: number;
  name?: string;
  color?: string;
  popup?: string;
}

export interface AladinCatalogOverlay {
  data: Array<{ ra: number; dec: number; [key: string]: unknown }>;
  color?: string;
  shape?: string;
}

export interface AladinViewerProps {
  ra?: number;
  dec?: number;
  fov?: number;
  survey?: string;
  markers?: AladinMarker[];
  catalogOverlay?: AladinCatalogOverlay;
  onPositionChange?: (ra: number, dec: number, fov: number) => void;
  onObjectClick?: (data: unknown) => void;
  height?: string;
  /** Legacy prop: array of objects to show as markers */
  objects?: Array<{
    name: string;
    ra: number;
    dec: number;
    source?: string;
    object_type?: string;
  }>;
  centerRa?: number;
  centerDec?: number;
}

/* ── CSS injection for Aladin Lite ── */

let cssInjected = false;
function ensureAladinCSS() {
  if (cssInjected) return;
  const existing = document.getElementById("aladin-css");
  if (existing) { cssInjected = true; return; }
  const link = document.createElement("link");
  link.id = "aladin-css";
  link.rel = "stylesheet";
  link.href = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.min.css";
  link.crossOrigin = "anonymous";
  document.head.appendChild(link);
  cssInjected = true;
}

/* ── Component ── */

export default function AladinViewer(props: AladinViewerProps) {
  const {
    ra: raProp,
    dec: decProp,
    fov: fovProp = 0.5,
    survey: surveyProp,
    markers,
    catalogOverlay,
    onPositionChange,
    onObjectClick,
    height = "400px",
    objects,
    centerRa,
    centerDec,
  } = props;

  // Resolve center: prefer ra/dec, then centerRa/centerDec, then first object/marker
  const resolvedRa = raProp ?? centerRa ?? (objects?.[0]?.ra) ?? (markers?.[0]?.ra) ?? 0;
  const resolvedDec = decProp ?? centerDec ?? (objects?.[0]?.dec) ?? (markers?.[0]?.dec) ?? 0;

  const containerRef = useRef<HTMLDivElement>(null);
  const containerId = useId().replace(/:/g, "-");
  const aladinRef = useRef<AladinInstance>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [statusMessage, setStatusMessage] = useState("Loading Aladin Lite...");
  const [selectedSurvey, setSelectedSurvey] = useState(surveyProp ?? "P/DSS2/color");
  const [coordDisplay, setCoordDisplay] = useState({ ra: resolvedRa, dec: resolvedDec });
  const initDoneRef = useRef(false);

  /* ── Annotation state ── */
  interface AnnotationCircle {
    ra: number;
    dec: number;
    radius_deg: number;
  }
  const [drawingMode, setDrawingMode] = useState(false);
  const [annotations, setAnnotations] = useState<AnnotationCircle[]>([]);
  const [radiusArcmin, setRadiusArcmin] = useState(1.0);
  const overlayRef = useRef<AladinInstance>(null);

  /** Create (or retrieve) the annotation graphic overlay. */
  const getOverlay = useCallback(() => {
    const aladin = aladinRef.current;
    if (!aladin || !window.A) return null;
    if (overlayRef.current) return overlayRef.current;
    const ov = window.A.graphicOverlay({ color: "#00ff88", lineWidth: 2 });
    aladin.addOverlay(ov);
    overlayRef.current = ov;
    return ov;
  }, []);

  /** Handle click on the Aladin container when drawing mode is active. */
  const handleViewerClick = useCallback(
    (e: ReactMouseEvent<HTMLDivElement>) => {
      if (!drawingMode) return;
      const aladin = aladinRef.current;
      if (!aladin) return;

      // Convert pixel coords to sky coords
      const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const lonlat: [number, number] | null = aladin.pix2world(x, y);
      if (!lonlat) return;
      const [ra, dec] = lonlat;
      const radDeg = radiusArcmin / 60;

      const ov = getOverlay();
      if (!ov || !window.A) return;
      ov.add(window.A.circle(ra, dec, radDeg, { color: "#00ff88", lineWidth: 2 }));

      setAnnotations((prev) => [...prev, { ra, dec, radius_deg: radDeg }]);
    },
    [drawingMode, radiusArcmin, getOverlay],
  );

  /** Clear all user annotations. */
  const handleClear = useCallback(() => {
    if (overlayRef.current) {
      overlayRef.current.removeAll();
    }
    setAnnotations([]);
  }, []);

  /** Export annotations as DS9 .reg file and trigger download. */
  const handleExportReg = useCallback(() => {
    if (annotations.length === 0) return;
    const lines = [
      "# Region file format: DS9 version 4.1",
      "global color=green",
      "fk5",
      ...annotations.map(
        (c) => `circle(${c.ra.toFixed(6)},${c.dec.toFixed(6)},${c.radius_deg.toFixed(6)}d)`,
      ),
    ];
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "annotations.reg";
    a.click();
    URL.revokeObjectURL(url);
  }, [annotations]);

  // Load the Aladin Lite script (once)
  useEffect(() => {
    let cancelled = false;
    ensureAladinCSS();

    async function loadScript() {
      setStatus("loading");
      setStatusMessage("Loading Aladin Lite...");

      let script = document.getElementById("aladin-script") as HTMLScriptElement | null;
      if (!script) {
        script = document.createElement("script");
        script.id = "aladin-script";
        script.src = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js";
        script.async = true;
        script.charset = "utf-8";
        script.crossOrigin = "anonymous";
        // T7 (PART T): SRI integrity for the Aladin CDN script.  The
        // `latest` URL means this hash needs updating whenever CDS ships
        // a new upstream version — on that day the script will fail to
        // load and we'll notice in the AladinViewer error UI. Trade-off
        // accepted: loud breakage on upstream change is safer than a
        // silent CDN-injection compromise.
        script.integrity = "sha384-z5VTZYsuT3SJA79XqRH6uvCKYmcEBee1vCGu9fle6dOUKcovX6SyeeSj5YsXYuyc";
        script.referrerPolicy = "no-referrer";
        document.head.appendChild(script);
      }

      await new Promise<void>((resolve, reject) => {
        if (window.A) { resolve(); return; }
        const handleLoad = () => resolve();
        const handleError = () => reject(new Error("Failed to load Aladin Lite script"));
        script!.addEventListener("load", handleLoad, { once: true });
        script!.addEventListener("error", handleError, { once: true });
        setTimeout(() => reject(new Error("Timed out waiting for Aladin Lite")), 15000);
      });

      if (window.A?.init) {
        await window.A.init;
      }

      if (cancelled) return;
      if (!window.A) {
        setStatus("error");
        setStatusMessage("Aladin Lite loaded incompletely. Refresh and try again.");
        return;
      }

      setStatus("ready");
      setStatusMessage("");
    }

    loadScript().catch((err: unknown) => {
      if (cancelled) return;
      setStatus("error");
      setStatusMessage(err instanceof Error ? err.message : "Failed to initialize sky viewer");
    });

    return () => { cancelled = true; };
  }, []);

  // Initialize the Aladin instance (once, when ready)
  useEffect(() => {
    if (status !== "ready" || !containerRef.current || !window.A || initDoneRef.current) return;

    containerRef.current.innerHTML = "";
    const aladin = window.A.aladin(`#${containerId}`, {
      survey: selectedSurvey,
      fov: fovProp,
      target: `${resolvedRa} ${resolvedDec}`,
      showReticle: true,
      showZoomControl: true,
      showFullscreenControl: true,
      showLayersControl: true,
      showGotoControl: true,
      cooFrame: "ICRSd",
    });
    aladinRef.current = aladin;
    initDoneRef.current = true;

    // Position change callback
    if (onPositionChange) {
      try {
        aladin.on("positionChanged", (pos: { ra: number; dec: number }) => {
          const currentFov = aladin.getFov?.()[0] ?? fovProp;
          onPositionChange(pos.ra, pos.dec, currentFov);
        });
      } catch {
        // Some versions don't support .on()
      }
    }

    // Object click callback
    if (onObjectClick) {
      try {
        aladin.on("objectClicked", (obj: unknown) => {
          onObjectClick(obj);
        });
      } catch {
        // Some versions don't support .on()
      }
    }

    // Mouse move -> coordinate readout
    try {
      aladin.on("positionChanged", (pos: { ra: number; dec: number }) => {
        setCoordDisplay({ ra: pos.ra, dec: pos.dec });
      });
    } catch {
      // fallback: no live readout
    }

    // PART Y Q3: capture container ref into a local at effect-start so the
    // cleanup uses the same DOM node we initialised on (the React-owned
    // ref may have been replaced by the time cleanup runs).
    const capturedContainer = containerRef.current;
    return () => {
      // Cleanup: Aladin Lite doesn't have a destroy method, but we can clear the container
      if (capturedContainer) {
        capturedContainer.innerHTML = "";
      }
      aladinRef.current = null;
      initDoneRef.current = false;
    };
    // Only run on initial ready
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, containerId]);

  // Update position when ra/dec/fov props change
  useEffect(() => {
    const aladin = aladinRef.current;
    if (!aladin) return;
    try {
      aladin.gotoRaDec(resolvedRa, resolvedDec);
    } catch { /* */ }
  }, [resolvedRa, resolvedDec]);

  useEffect(() => {
    const aladin = aladinRef.current;
    if (!aladin) return;
    try {
      aladin.setFov(fovProp);
    } catch { /* */ }
  }, [fovProp]);

  // Add markers from the `objects` prop (legacy) or `markers` prop
  const addMarkers = useCallback(() => {
    const aladin = aladinRef.current;
    if (!aladin || !window.A) return;

    // Use objects prop if present (legacy interface from DataBrowser)
    if (objects && objects.length > 0) {
      // Group by source for color coding
      const bySource: Record<string, typeof objects> = {};
      for (const obj of objects) {
        const src = obj.source?.toLowerCase() ?? "unknown";
        if (!bySource[src]) bySource[src] = [];
        bySource[src].push(obj);
      }

      for (const [source, srcObjects] of Object.entries(bySource)) {
        const color = getSourceColor(source);
        const cat = window.A.catalog({
          name: source.toUpperCase(),
          sourceSize: 14,
          color,
          shape: "circle",
        });

        const sources = srcObjects.map((obj) =>
          window.A!.marker(obj.ra, obj.dec, {
            popupTitle: obj.name,
            popupDesc: `${source.toUpperCase()}${obj.object_type ? ` | ${obj.object_type}` : ""}`,
          })
        );
        cat.addSources(sources);
        aladin.addCatalog(cat);
      }
      return;
    }

    // Use markers prop
    if (markers && markers.length > 0) {
      const cat = window.A.catalog({
        name: "Markers",
        sourceSize: 14,
        color: markers[0].color ?? "#0A84FF",
        shape: "circle",
      });

      const sources = markers.map((m) =>
        window.A!.marker(m.ra, m.dec, {
          popupTitle: m.name ?? `${m.ra.toFixed(4)}, ${m.dec.toFixed(4)}`,
          popupDesc: m.popup ?? "",
        })
      );
      cat.addSources(sources);
      aladin.addCatalog(cat);
    }

    // Catalog overlay
    if (catalogOverlay && catalogOverlay.data.length > 0) {
      const cat = window.A.catalog({
        name: "Catalog",
        sourceSize: 10,
        color: catalogOverlay.color ?? "#ffcc00",
        shape: catalogOverlay.shape ?? "plus",
      });

      const sources = catalogOverlay.data.map((d) =>
        window.A!.marker(d.ra, d.dec, {
          popupTitle: `${d.ra.toFixed(4)}, ${d.dec.toFixed(4)}`,
          popupDesc: "",
        })
      );
      cat.addSources(sources);
      aladin.addCatalog(cat);
    }
  }, [objects, markers, catalogOverlay]);

  // Trigger marker updates when data or init status changes
  useEffect(() => {
    if (status === "ready" && initDoneRef.current) {
      addMarkers();
    }
  }, [status, addMarkers]);

  // Survey change handler
  const handleSurveyChange = (newSurvey: string) => {
    setSelectedSurvey(newSurvey);
    const aladin = aladinRef.current;
    if (aladin) {
      try {
        aladin.setImageSurvey(newSurvey);
      } catch {
        // fallback
        try {
          aladin.setBaseImageLayer(newSurvey);
        } catch { /* */ }
      }
    }
  };

  // Format RA/Dec for display
  const formatRA = (ra: number) => {
    const h = Math.floor(ra / 15);
    const m = Math.floor((ra / 15 - h) * 60);
    const s = ((ra / 15 - h - m / 60) * 3600).toFixed(2);
    return `${h}h ${m}m ${s}s`;
  };

  const formatDec = (dec: number) => {
    const sign = dec >= 0 ? "+" : "-";
    const absDec = Math.abs(dec);
    const d = Math.floor(absDec);
    const m = Math.floor((absDec - d) * 60);
    const s = ((absDec - d - m / 60) * 3600).toFixed(1);
    return `${sign}${d}d ${m}' ${s}"`;
  };

  return (
    <div style={{ position: "relative" }}>
      {/* Survey selector */}
      {status === "ready" && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 6,
            fontSize: "0.8rem",
          }}
        >
          <label style={{ color: "var(--color-text-secondary)", fontWeight: 500 }}>
            Survey:
          </label>
          <select
            value={selectedSurvey}
            onChange={(e) => handleSurveyChange(e.target.value)}
            style={{
              padding: "3px 8px",
              borderRadius: 6,
              border: "1px solid var(--color-separator)",
              background: "var(--color-bg-secondary, #1a1a1a)",
              color: "var(--color-text, #fff)",
              fontSize: "0.78rem",
              cursor: "pointer",
            }}
          >
            {SURVEYS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Loading / error overlay */}
      {(status === "loading" || status === "error") && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "grid",
            placeItems: "center",
            zIndex: 1,
            color: status === "error" ? "var(--color-red)" : "var(--color-text-secondary)",
            background: "rgba(0,0,0,0.35)",
            borderRadius: "var(--radius-md)",
            textAlign: "center",
            padding: "1rem",
          }}
        >
          {statusMessage}
        </div>
      )}

      {/* Aladin container */}
      <div
        id={containerId}
        ref={containerRef}
        onClick={handleViewerClick}
        style={{
          width: "100%",
          height: typeof height === "number" ? height : height,
          borderRadius: "var(--radius-md)",
          overflow: "hidden",
          border: "1px solid var(--color-separator)",
          background: "#111",
          cursor: drawingMode ? "crosshair" : undefined,
        }}
      />

      {/* Coordinate readout bar */}
      {status === "ready" && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "4px 10px",
            fontSize: "0.75rem",
            fontFamily: "monospace",
            color: "var(--color-text-secondary)",
            background: "var(--color-bg-secondary, #1a1a1a)",
            border: "1px solid var(--color-separator)",
            borderTop: "none",
            marginTop: -1,
          }}
        >
          <span>
            RA: {formatRA(coordDisplay.ra)} ({coordDisplay.ra.toFixed(5)}&deg;)
          </span>
          <span>
            Dec: {formatDec(coordDisplay.dec)} ({coordDisplay.dec.toFixed(5)}&deg;)
          </span>
        </div>
      )}

      {/* Annotation toolbar */}
      {status === "ready" && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "4px 10px",
            fontSize: "0.75rem",
            background: "var(--color-bg-secondary, #1a1a1a)",
            borderRadius: "0 0 var(--radius-md) var(--radius-md)",
            border: "1px solid var(--color-separator)",
            borderTop: "none",
            marginTop: -1,
          }}
        >
          <button
            className={`btn-secondary btn-small${drawingMode ? " active" : ""}`}
            onClick={() => setDrawingMode((m) => !m)}
            style={drawingMode ? { background: "rgba(0,255,136,0.18)", color: "#00ff88" } : undefined}
          >
            {drawingMode ? "Drawing..." : "Draw Circle"}
          </button>

          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 3,
              color: "var(--color-text-secondary)",
              fontSize: "0.72rem",
            }}
          >
            r&nbsp;
            <input
              type="number"
              min={0.01}
              step={0.1}
              value={radiusArcmin}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!isNaN(v) && v > 0) setRadiusArcmin(v);
              }}
              style={{
                width: 48,
                padding: "1px 4px",
                borderRadius: 4,
                border: "1px solid var(--color-separator)",
                background: "var(--color-bg-tertiary, #222)",
                color: "var(--color-text)",
                fontSize: "0.72rem",
                textAlign: "right",
              }}
            />
            &prime;
          </label>

          <button
            className="btn-secondary btn-small"
            onClick={handleClear}
            disabled={annotations.length === 0}
          >
            Clear
          </button>
          <button
            className="btn-secondary btn-small"
            onClick={handleExportReg}
            disabled={annotations.length === 0}
          >
            Export .reg
          </button>

          {annotations.length > 0 && (
            <span style={{ marginLeft: "auto", color: "var(--color-text-secondary)", fontSize: "0.7rem" }}>
              {annotations.length} region{annotations.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      )}

      {/* Region info list */}
      {status === "ready" && annotations.length > 0 && (
        <div
          style={{
            maxHeight: 90,
            overflowY: "auto",
            padding: "4px 10px",
            fontSize: "0.7rem",
            fontFamily: "monospace",
            color: "var(--color-text-secondary)",
            background: "var(--color-bg-secondary, #1a1a1a)",
            borderRadius: "0 0 var(--radius-md) var(--radius-md)",
            border: "1px solid var(--color-separator)",
            borderTop: "none",
            marginTop: -1,
            lineHeight: 1.6,
          }}
        >
          {annotations.map((c, i) => (
            <div key={i}>
              Circle: RA={c.ra.toFixed(2)}&deg;, Dec={c.dec.toFixed(2)}&deg;, r=
              {c.radius_deg.toFixed(4)}&deg; ({(c.radius_deg * 60).toFixed(1)}&prime;)
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
