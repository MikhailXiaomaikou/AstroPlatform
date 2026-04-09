/**
 * Aladin Lite sky viewer — embeds an interactive HiPS sky map
 * with search result overlay markers.
 */

import { useEffect, useRef } from "react";

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
      aladin: (el: HTMLElement, opts: Record<string, unknown>) => {
        gotoRaDec: (ra: number, dec: number) => void;
        setFov: (fov: number) => void;
        addCatalog: (cat: unknown) => void;
      };
      catalog: (opts: Record<string, unknown>) => {
        addSources: (sources: unknown[]) => void;
      };
      source: (ra: number, dec: number, data: Record<string, string>) => unknown;
    };
  }
}

export default function AladinViewer({ objects, centerRa, centerDec, fov = 1.0 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const aladinRef = useRef<ReturnType<NonNullable<typeof window.A>["aladin"]> | null>(null);

  // Load Aladin Lite script
  useEffect(() => {
    if (document.getElementById("aladin-script")) return;

    // CSS
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.min.css";
    document.head.appendChild(link);

    // JS
    const script = document.createElement("script");
    script.id = "aladin-script";
    script.src = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.min.js";
    script.async = true;
    script.charset = "utf-8";
    document.head.appendChild(script);
  }, []);

  // Initialize Aladin once script is loaded
  useEffect(() => {
    if (!containerRef.current) return;

    const initAladin = () => {
      if (!window.A || !containerRef.current) return;

      const ra = centerRa ?? (objects.length > 0 ? objects[0].ra : 0);
      const dec = centerDec ?? (objects.length > 0 ? objects[0].dec : 0);

      const aladin = window.A.aladin(containerRef.current, {
        survey: "P/DSS2/color",
        fov,
        target: `${ra} ${dec}`,
        showReticle: true,
        showZoomControl: true,
        showFullscreenControl: true,
        showLayersControl: true,
        showGotoControl: true,
        cooFrame: "J2000",
      });
      aladinRef.current = aladin;

      // Add catalog overlay
      if (objects.length > 0) {
        const cat = window.A.catalog({
          name: "Search Results",
          sourceSize: 14,
          color: "#0A84FF",
          shape: "circle",
        });

        const sources = objects.map((obj) =>
          window.A!.source(obj.ra, obj.dec, {
            name: obj.name,
            type: obj.object_type || "",
            source: obj.source || "",
          })
        );
        cat.addSources(sources);
        aladin.addCatalog(cat);
      }
    };

    // Wait for Aladin to load
    if (window.A) {
      initAladin();
    } else {
      const check = setInterval(() => {
        if (window.A) {
          clearInterval(check);
          initAladin();
        }
      }, 200);
      const timeout = setTimeout(() => clearInterval(check), 10000);
      return () => { clearInterval(check); clearTimeout(timeout); };
    }
  }, [objects, centerRa, centerDec, fov]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: 400,
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
        border: "1px solid var(--color-separator)",
      }}
    />
  );
}
