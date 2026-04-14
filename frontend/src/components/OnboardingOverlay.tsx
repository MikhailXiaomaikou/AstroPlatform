import { useState, useEffect, useCallback } from "react";
import { useI18n } from "../i18n";

const STEP_KEYS = [
  { titleKey: "onboard.search_title", descKey: "onboard.search_desc", targetSelector: 'a[href="/search"]' },
  { titleKey: "onboard.chat_title", descKey: "onboard.chat_desc", targetSelector: 'a[href="/chat"]' },
  { titleKey: "onboard.pipeline_title", descKey: "onboard.pipeline_desc", targetSelector: 'a[href="/pipeline"]' },
  { titleKey: "onboard.adql_title", descKey: "onboard.adql_desc", targetSelector: 'a[href="/adql"]' },
];

export default function OnboardingOverlay() {
  const { t } = useI18n();
  const [visible, setVisible] = useState(false);
  const [step, setStep] = useState(0);
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (!localStorage.getItem("astro_onboarded")) {
      const timer = setTimeout(() => setVisible(true), 500);
      return () => clearTimeout(timer);
    }
  }, []);

  const updateTargetRect = useCallback(() => {
    const el = document.querySelector(STEP_KEYS[step].targetSelector);
    if (el) setTargetRect(el.getBoundingClientRect());
  }, [step]);

  useEffect(() => {
    if (visible) {
      updateTargetRect();
      window.addEventListener("resize", updateTargetRect);
      return () => window.removeEventListener("resize", updateTargetRect);
    }
  }, [visible, step, updateTargetRect]);

  const handleComplete = () => {
    localStorage.setItem("astro_onboarded", "1");
    setVisible(false);
  };

  const handleNext = () => {
    if (step < STEP_KEYS.length - 1) setStep(step + 1);
    else handleComplete();
  };

  const handleBack = () => {
    if (step > 0) setStep(step - 1);
  };

  if (!visible) return null;

  const current = STEP_KEYS[step];
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
          {t("onboard.step_of")} {step + 1} {t("onboard.of")} {STEP_KEYS.length}
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
              {step < STEP_KEYS.length - 1 ? t("onboard.next") : t("onboard.start")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
