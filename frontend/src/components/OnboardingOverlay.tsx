import { useState, useEffect, useCallback } from "react";
import { useI18n } from "../i18n";
import { TOUR_ROUTES } from "../routes";

// History: the previous static STEP_KEYS array referenced 3 deleted routes
// (/search, /pipeline, /adql) — document.querySelector on those selectors
// returned null and silently broke the tour. The current tour is sourced
// from TOUR_ROUTES (frontend/src/routes.ts) so it always tracks the live
// nav. A new tour step is added by setting `tourSelector` + `descKey` in
// routes.ts; no edit to this file required.

interface OnboardingStep {
  titleKey: string;
  descKey: string;
  targetSelector: string;
}

function deriveSteps(): OnboardingStep[] {
  return TOUR_ROUTES.filter((route) => route.tourSelector && route.descKey).map(
    (route) => ({
      // Convention: every onboarding doc pair shares a stem.
      // onboard.<name>_desc lives in routes.ts; the title key flips the suffix.
      titleKey: route.descKey!.replace("_desc", "_title"),
      descKey: route.descKey!,
      targetSelector: route.tourSelector!,
    }),
  );
}

export default function OnboardingOverlay() {
  const { t } = useI18n();
  const [visible, setVisible] = useState(false);
  const [step, setStep] = useState(0);
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null);

  const steps = deriveSteps();

  useEffect(() => {
    if (steps.length === 0) return;
    if (!localStorage.getItem("astro_onboarded")) {
      const timer = setTimeout(() => setVisible(true), 500);
      return () => clearTimeout(timer);
    }
  }, [steps.length]);

  const updateTargetRect = useCallback(() => {
    if (step >= steps.length) return;
    const el = document.querySelector(steps[step].targetSelector);
    if (el) setTargetRect(el.getBoundingClientRect());
    else setTargetRect(null);
  }, [step, steps]);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    // Q3: updateTargetRect reads DOM getBoundingClientRect and calls
    // setTargetRect — must run after DOM commit, not during render.
    if (visible) {
      updateTargetRect();
      window.addEventListener("resize", updateTargetRect);
      return () => window.removeEventListener("resize", updateTargetRect);
    }
  }, [visible, step, updateTargetRect]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleComplete = () => {
    localStorage.setItem("astro_onboarded", "1");
    setVisible(false);
  };

  const handleNext = () => {
    if (step < steps.length - 1) setStep(step + 1);
    else handleComplete();
  };

  const handleBack = () => {
    if (step > 0) setStep(step - 1);
  };

  if (!visible || steps.length === 0) return null;

  const current = steps[step];
  const tooltipStyle: React.CSSProperties = targetRect
    ? { position: "fixed", top: targetRect.bottom + 12, left: Math.max(16, targetRect.left - 100) }
    : { position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)" };

  return (
    <div className="onboarding-backdrop">
      {targetRect && (
        <div
          className="onboarding-highlight"
          style={{
            position: "fixed",
            top: targetRect.top - 4,
            left: targetRect.left - 4,
            width: targetRect.width + 8,
            height: targetRect.height + 8,
            borderRadius: 8,
          }}
        />
      )}
      <div className="onboarding-tooltip" style={tooltipStyle} role="dialog" aria-modal="true" aria-labelledby="onboarding-title" aria-describedby="onboarding-desc">
        <div className="onboarding-step-indicator">
          {t("onboard.step_of")} {step + 1} {t("onboard.of")} {steps.length}
        </div>
        <h3 id="onboarding-title">{t(current.titleKey)}</h3>
        <p id="onboarding-desc">{t(current.descKey)}</p>
        <div className="onboarding-actions">
          <button className="btn-ghost btn-small" onClick={handleComplete}>
            {t("onboard.skip")}
          </button>
          <div style={{ display: "flex", gap: 8 }}>
            {step > 0 && (
              <button className="btn-secondary btn-small" onClick={handleBack}>
                {t("onboard.back")}
              </button>
            )}
            <button className="btn-primary btn-small" onClick={handleNext}>
              {step < steps.length - 1 ? t("onboard.next") : t("onboard.start")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
