import { useState, useEffect, useCallback } from "react";

interface Step {
  title: string;
  description: string;
  targetSelector: string;
}

const STEPS: Step[] = [
  {
    title: "Search the Universe",
    description:
      "Query 19 astronomical databases simultaneously. Enter any object name, coordinates, or use natural language like 'galaxies with redshift > 0.5'.",
    targetSelector: '.top-nav a:not(.logo)[href="/"]',
  },
  {
    title: "AI Research Assistant",
    description:
      "Chat with an AI that understands astronomy. It can search catalogs, run ADQL queries, generate pipelines, and help write papers.",
    targetSelector: 'a[href="/chat"]',
  },
  {
    title: "Visual Pipeline Builder",
    description:
      "Build data processing workflows by dragging nodes. Denoise, fit spectra, cross-match catalogs, and more — all connected in a visual DAG.",
    targetSelector: 'a[href="/pipeline"]',
  },
  {
    title: "Direct Database Access",
    description:
      "Write ADQL queries against Gaia, SIMBAD, VizieR, and CADC TAP services with syntax highlighting and template queries.",
    targetSelector: 'a[href="/adql"]',
  },
];

export default function OnboardingOverlay() {
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
    const el = document.querySelector(STEPS[step].targetSelector);
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
    if (step < STEPS.length - 1) setStep(step + 1);
    else handleComplete();
  };

  const handleBack = () => {
    if (step > 0) setStep(step - 1);
  };

  if (!visible) return null;

  const currentStep = STEPS[step];
  const tooltipStyle: React.CSSProperties = targetRect
    ? {
        position: "fixed",
        top: targetRect.bottom + 12,
        left: Math.max(16, targetRect.left - 100),
      }
    : {
        position: "fixed",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
      };

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
      <div className="onboarding-tooltip" style={tooltipStyle}>
        <div className="onboarding-step-indicator">
          Step {step + 1} of {STEPS.length}
        </div>
        <h3>{currentStep.title}</h3>
        <p>{currentStep.description}</p>
        <div className="onboarding-actions">
          <button className="btn-ghost btn-small" onClick={handleComplete}>
            Skip Tour
          </button>
          <div style={{ display: "flex", gap: 8 }}>
            {step > 0 && (
              <button className="btn-secondary btn-small" onClick={handleBack}>
                Back
              </button>
            )}
            <button className="btn-primary btn-small" onClick={handleNext}>
              {step < STEPS.length - 1 ? "Next" : "Get Started"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
