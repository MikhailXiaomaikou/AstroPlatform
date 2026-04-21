import { useState, useEffect } from "react";
import { getObjectDetail, saveObject } from "../api/client";
import type { ObjectDetail } from "../api/client";
import { useI18n } from "../i18n";

interface Props {
  objectName: string;
  ra?: number;
  dec?: number;
  isOpen: boolean;
  onClose: () => void;
}

export default function ObjectDetailPanel({ objectName, ra, dec, isOpen, onClose }: Props) {
  const { t } = useI18n();
  const [data, setData] = useState<ObjectDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "surveys" | "refs" | "data">("overview");
  const [saved, setSaved] = useState(false);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    // Q3: panel 打开时重置 loading/error/data/tab 然后发 async fetch.
    // 经典 "props change → reset state → kick off request" 场景.
    if (!isOpen || !objectName) return;
    setLoading(true);
    setError(null);
    setData(null);
    setTab("overview");
    getObjectDetail(objectName, ra, dec)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : t("odp.failed_to_load")))
      .finally(() => setLoading(false));
  }, [isOpen, objectName, ra, dec]);
  /* eslint-enable react-hooks/set-state-in-effect */

  if (!isOpen) return null;

  return (
    <>
      <div className="odp-backdrop" onClick={onClose} />
      <div className="odp-panel">
        <div className="odp-header">
          <button className="odp-close" onClick={onClose}>X</button>
          {data ? (
            <>
              <h2 className="odp-title">{data.name}</h2>
              <div className="odp-subtitle">
                {data.object_type_long || data.object_type}
                {data.redshift != null && <span className="odp-z"> z = {data.redshift.toFixed(6)}</span>}
              </div>
              <div className="odp-coords">
                RA {data.ra.toFixed(5)}&deg; &nbsp; Dec {data.dec.toFixed(5)}&deg;
                <button
                  className={`odp-save-btn${saved ? " saved" : ""}`}
                  onClick={() => {
                    saveObject({
                      name: data.name, ra: data.ra, dec: data.dec,
                      object_type: data.object_type, redshift: data.redshift ?? undefined,
                    }).then(() => setSaved(true)).catch(() => {});
                  }}
                  disabled={saved}
                  title={saved ? t("odp.saved_to_bookmarks") : t("odp.save_to_bookmarks")}
                >
                  {saved ? t("common.saved") : t("common.save")}
                </button>
              </div>
            </>
          ) : (
            <h2 className="odp-title">{objectName}</h2>
          )}
        </div>

        {loading && <div className="odp-loading">{t("odp.loading")}</div>}
        {error && <div className="odp-error">{error}</div>}

        {data && (
          <>
            <div className="odp-tabs">
              {(["overview", "surveys", "refs", "data"] as const).map((tabKey) => (
                <button key={tabKey} className={`odp-tab${tab === tabKey ? " active" : ""}`} onClick={() => setTab(tabKey)}>
                  {tabKey === "overview" ? t("odp.overview") : tabKey === "surveys" ? t("odp.surveys") : tabKey === "refs" ? `${t("odp.references")}${data.references.length ? ` (${data.references.length})` : ""}` : t("odp.raw_data")}
                </button>
              ))}
            </div>

            <div className="odp-body">
              {tab === "overview" && (
                <div className="odp-overview">
                  <table className="odp-info-table">
                    <tbody>
                      {data.spectral_type && <tr><td className="odp-label">{t("odp.spectral_type")}</td><td>{data.spectral_type}</td></tr>}
                      {data.morphology && <tr><td className="odp-label">{t("odp.morphology")}</td><td>{data.morphology}</td></tr>}
                      {data.redshift != null && <tr><td className="odp-label">{t("odp.redshift")}</td><td>{data.redshift.toFixed(6)}</td></tr>}
                      {data.radial_velocity != null && <tr><td className="odp-label">{t("odp.radial_velocity")}</td><td>{data.radial_velocity.toFixed(1)} km/s</td></tr>}
                      {data.parallax != null && <tr><td className="odp-label">{t("odp.parallax")}</td><td>{data.parallax.toFixed(2)} mas</td></tr>}
                      {data.proper_motion_ra != null && <tr><td className="odp-label">{t("odp.pm_ra")}</td><td>{data.proper_motion_ra.toFixed(2)} mas/yr</td></tr>}
                      {data.proper_motion_dec != null && <tr><td className="odp-label">{t("odp.pm_dec")}</td><td>{data.proper_motion_dec.toFixed(2)} mas/yr</td></tr>}
                    </tbody>
                  </table>

                  {data.cross_ids.length > 0 && (
                    <div className="odp-section">
                      <h4 className="odp-section-title">{t("odp.cross_ids")} ({data.cross_ids.length})</h4>
                      <div className="odp-cross-ids">
                        {data.cross_ids.map((ci, i) => (
                          <span key={i} className="odp-cross-id">{ci.name}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {tab === "surveys" && (
                <div className="odp-surveys">
                  {data.surveys.length === 0 && <p className="odp-empty">{t("odp.no_survey_data")}</p>}
                  {data.surveys.map((s) => (
                    <div key={s.source} className={`odp-survey-row${s.has_data ? "" : " no-data"}`}>
                      <span className={`badge badge-${s.source}`}>{s.source.toUpperCase()}</span>
                      <span className="odp-survey-count">
                        {s.has_data ? `${s.count} object${s.count !== 1 ? "s" : ""}` : t("odp.no_data")}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {tab === "refs" && (
                <div className="odp-refs">
                  {data.references.length === 0 && <p className="odp-empty">{t("odp.no_refs")}</p>}
                  {data.references.map((ref) => (
                    <div key={ref.bibcode} className="odp-ref">
                      <a href={`https://ui.adsabs.harvard.edu/abs/${ref.bibcode}`} target="_blank" rel="noopener noreferrer" className="odp-ref-title">
                        {ref.title}
                      </a>
                      <div className="odp-ref-meta">
                        {ref.authors.slice(0, 3).join(", ")}{ref.authors.length > 3 ? " et al." : ""} ({ref.year})
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {tab === "data" && (
                <div className="odp-raw-data">
                  {Object.keys(data.all_data).length === 0 && <p className="odp-empty">{t("odp.no_raw_data")}</p>}
                  {Object.entries(data.all_data).map(([source, items]) => (
                    <details key={source} className="odp-data-section">
                      <summary>
                        <span className={`badge badge-${source}`}>{source.toUpperCase()}</span>
                        {" "}{items.length} {t("odp.results")}
                      </summary>
                      <div className="odp-data-list">
                        {items.map((item, i) => {
                          const r = item as unknown as Record<string, unknown>;
                          return (
                            <div key={i} className="odp-data-item">
                              <span>{String(r.name || r.object_id)}</span>
                              <span className="odp-data-coords">
                                {Number(r.ra).toFixed(4)}, {Number(r.dec).toFixed(4)}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </details>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </>
  );
}
