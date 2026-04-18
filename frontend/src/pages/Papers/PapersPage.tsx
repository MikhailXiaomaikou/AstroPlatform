import { useI18n } from "../../i18n";

/**
 * Papers page stub — matches demo #page-papers layout.
 * Full manuscript editor + compile pipeline land in Phase J4.
 */

export default function PapersPage() {
  const { t } = useI18n();

  return (
    <div className="journal-page">
      <div className="page-head">
        <div>
          <h1>{t("nav.papers")}</h1>
          <div className="page-sub">
            <em>aastex 631 · bibstyle aasjournal · draft manuscripts from your sessions</em>
          </div>
        </div>
        <div className="badge-row">
          <span className="j-tag verified">✓ 0 uncited</span>
        </div>
      </div>

      <div className="three-col">
        <aside className="side-left">
          <div className="j-side-block">
            <h4>Manuscripts</h4>
            <a className="j-side-item active">
              <span>NGC 752 · cluster age</span>
              <em>draft · 14 refs</em>
            </a>
            <a className="j-side-item">
              <span>M13 · Bayesian age</span>
              <em>draft · 22 refs</em>
            </a>
            <div className="j-side-group">Submitted</div>
            <a className="j-side-item">
              <span>QSO BH mass set</span>
              <em>2026-02-14</em>
            </a>
          </div>
          <div className="j-side-block">
            <h4>Templates</h4>
            <a className="j-side-item"><span>aastex 631</span><em>default</em></a>
            <a className="j-side-item"><span>mnras</span></a>
            <a className="j-side-item"><span>A&amp;A longauthor</span></a>
          </div>
        </aside>

        <main>
          <div className="page-head">
            <div>
              <h1>NGC 752 · draft</h1>
              <div className="page-sub">
                <em>14 refs · 1 figure · 1 table · ✓ 0 uncited</em>
              </div>
            </div>
            <div className="badge-row">
              <button className="btn-journal-ghost">Compile</button>
              <button className="btn-journal-ghost">Download .tex</button>
              <button className="btn-journal-primary">Open in Overleaf →</button>
            </div>
          </div>

          <div className="manuscript">
            <div className="ms-page">
              <div className="ms-title">A turnoff-based age for NGC 752 from Gaia DR3 photometry</div>
              <div className="ms-authors">K. Chen and Standard Astro (collab.)</div>
              <div className="ms-affil">
                <em>Dept. of Astronomy · OpenClaw Research &nbsp;·&nbsp; Standard Astro Platform</em>
              </div>
              <div className="ms-abstract">
                <strong>Abstract —</strong> We retrieve 176 high-probability members of the open cluster NGC 752
                from Gaia DR3 via a proper-motion plus parallax membership cut and fit a PARSEC 1.2S isochrone
                with a two-stage grid search. We obtain an age of 1.56 Gyr at a distance of 441 pc with
                [Fe/H] = +0.02, consistent with the literature.
              </div>
              <div className="ms-twocol">
                <div>
                  <h4>1. Introduction</h4>
                  <p>The open cluster NGC 752 has long served as a calibrator for intermediate-age main-sequence
                    evolution. Its proximity and modest reddening make it a natural benchmark for Gaia-era
                    photometric ages.</p>
                  <h4>2. Data</h4>
                  <p>All photometry and astrometry are drawn from Gaia DR3. We query the cluster field with a
                    0.5° cone and apply proper-motion envelopes around (pmα, pmδ) = (+9.8, −11.8) mas yr⁻¹.</p>
                </div>
                <div>
                  <h4>3. Method</h4>
                  <p>The main-sequence turnoff is identified by binning the dereddened BP−RP axis in 0.10-mag
                    bins; the bluest bin containing ≥3 stars yields the turnoff colour and median M_G.</p>
                  <h4>4. Result</h4>
                  <p>The best-fit is age = 1.56 Gyr, d = 441 pc, [Fe/H] = +0.02, A_V = 0.12, with χ²_red = 1.08.</p>
                </div>
              </div>
            </div>
          </div>

          <div className="compile-log">
            <div className="cl-head">Compile log · aastex631</div>
            <pre className="cl-body">{`[LaTeX] aastex631 v1.0.4
[refs ] resolved 14/14 bibcodes via ADS
[fig  ] embedded Fig. 1 at 300 DPI
[gate ] claim_validator: 0 uncited numeric claims
[done ] output: ms_ngc752.pdf (6 pages, 312 KB)`}</pre>
          </div>
        </main>

        <aside className="side-right">
          <div className="j-side-block">
            <h4>Figures</h4>
            <div className="thumb">
              <div className="thumb-img"><span>CMD · NGC 752</span></div>
              <div className="thumb-caption"><em>Fig. 1</em> · column width · PDF 300 DPI</div>
            </div>
          </div>
          <div className="j-side-block">
            <h4>References</h4>
            <ol className="ref-list">
              <li>Bressan A. <em>et al.</em> 2012, MNRAS, 427, 127.</li>
              <li>Cantat-Gaudin T. <em>et al.</em> 2020, A&amp;A, 640, A1.</li>
              <li>Twarog B. A. <em>et al.</em> 1997, AJ, 114, 2556.</li>
              <li>Agüeros M. A. <em>et al.</em> 2018, ApJ, 862, 33.</li>
              <li>Gaia Collaboration 2023, A&amp;A, 674, A1.</li>
            </ol>
          </div>
          <div className="j-side-block">
            <h4>Reproducibility</h4>
            <div className="row"><span className="k">tool_version</span><span className="v">fd19886</span></div>
            <div className="row"><span className="k">seed</span><span className="v">20260417</span></div>
            <div className="row"><span className="k">run_id</span><span className="v">7f2a9c…</span></div>
          </div>
        </aside>
      </div>
    </div>
  );
}
