import { useEffect, useId, useRef, useState } from "react";

interface AladinObject {
  name: string;
  ra: number;
  dec: number;
  source?: string;
  object_type?: string;
}

interface Props {
  objects: AladinObject[];
  centerRa?: number;
  centerDec?: number;
  fov?: number;
}

declare global {
  interface Window {
    A?: {
      init?: Promise<void>;
      aladin: (selector: string, opts: Record<string, unknown>) => {
        gotoRaDec: (ra: number, dec: number) => void;
        setFov: (fov: number) => void;
        addCatalog: (cat: unknown) => void;
      };
      catalog: (opts: Record<string, unknown>) => {
        addSources: (sources: unknown[]) => void;
      };
      marker: (ra: number, dec: number, data: Record<string, string>) => unknown;
    };
  }
}

export default function AladinViewer({ objects, centerRa, centerDec, fov = 1.0 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const containerId = useId().replace(/:/g, "-");
  const aladinRef = useRef<ReturnType<NonNullable<typeof window.A>["aladin"]> | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [statusMessage, setStatusMessage] = useState("Loading Aladin Lite...");

  useEffect(() => {
    let cancelled = false;

    async function ensureAladin() {
      setStatus("loading");
      setStatusMessage("Loading Aladin Lite...");

      let script = document.getElementById("aladin-script") as HTMLScriptElement | null;
      if (!script) {
        script = document.createElement("script");
        script.id = "aladin-script";
        script.src = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js";
        script.async = true;
        script.charset = "utf-8";
        document.head.appendChild(script);
      }

      await new Promise<void>((resolve, reject) => {
        if (window.A) {
          resolve();
          return;
        }
        const handleLoad = () => resolve();
        const handleError = () => reject(new Error("Failed to load Aladin Lite script"));
        script!.addEventListener("load", handleLoad, { once: true });
        script!.addEventListener("error", handleError, { once: true });
        setTimeout(() => reject(new Error("Timed out waiting for Aladin Lite")), 10000);
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

    ensureAladin().catch((err: unknown) => {
      if (cancelled) return;
      setStatus("error");
      setStatusMessage(err instanceof Error ? err.message : "Failed to initialize sky viewer");
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (status !== "ready" || !containerRef.current || !window.A) return;

    const ra = centerRa ?? (objects.length > 0 ? objects[0].ra : 0);
    const dec = centerDec ?? (objects.length > 0 ? objects[0].dec : 0);

    containerRef.current.innerHTML = "";
    const aladin = window.A.aladin(`#${containerId}`, {
      survey: "P/DSS2/color",
      fov,
      target: `${ra} ${dec}`,
      showReticle: true,
      showZoomControl: true,
      showFullscreenControl: true,
      showLayersControl: true,
      showGotoControl: true,
      cooFrame: "ICRSd",
    });
    aladinRef.current = aladin;

    if (objects.length > 0) {
      const cat = window.A.catalog({
        name: "Search Results",
        sourceSize: 14,
        color: "#0A84FF",
        shape: "circle",
      });

      const sources = objects.map((obj) =>
        window.A!.marker(obj.ra, obj.dec, {
          popupTitle: obj.name,
          popupDesc: `${obj.source?.toUpperCase() || "ARCHIVE"}${obj.object_type ? ` • ${obj.object_type}` : ""}`,
        })
      );
      cat.addSources(sources);
      aladin.addCatalog(cat);
    }
  }, [centerDec, centerRa, containerId, fov, objects, status]);

  return (
    <div style={{ position: "relative" }}>
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
      <div
        id={containerId}
        ref={containerRef}
        style={{
          width: "100%",
          height: 400,
          borderRadius: "var(--radius-md)",
          overflow: "hidden",
          border: "1px solid var(--color-separator)",
          background: "#111",
        }}
      />
    </div>
  );
}
