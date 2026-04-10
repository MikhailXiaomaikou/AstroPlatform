import { useState, useRef, useEffect, useCallback } from "react";
import { GLOSSARY } from "../data/glossary";
import { useI18n } from "../i18n";

interface Props {
  term: string;
  children: React.ReactNode;
}

export default function GlossaryTerm({ term, children }: Props) {
  const { lang } = useI18n();
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState<"above" | "below">("above");
  const ref = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLSpanElement>(null);

  const entry = GLOSSARY.find(
    (g) => g.term.toLowerCase() === term.toLowerCase(),
  );
  const definition = entry
    ? lang === "zh"
      ? entry.zh
      : entry.en
    : term;

  const reposition = useCallback(() => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    setPos(rect.top < 120 ? "below" : "above");
  }, []);

  useEffect(() => {
    if (visible) reposition();
  }, [visible, reposition]);

  return (
    <span
      ref={ref}
      className="glossary-term-wrapper"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
      tabIndex={0}
      style={{ position: "relative", display: "inline" }}
    >
      <span
        style={{
          borderBottom: "1.5px dotted var(--color-text-secondary)",
          cursor: "help",
        }}
      >
        {children}
      </span>
      {visible && (
        <span
          ref={tipRef}
          style={{
            position: "absolute",
            left: "50%",
            transform: "translateX(-50%)",
            ...(pos === "above"
              ? { bottom: "calc(100% + 8px)" }
              : { top: "calc(100% + 8px)" }),
            background: "var(--color-surface-2)",
            color: "var(--color-text)",
            padding: "8px 12px",
            borderRadius: "var(--radius-sm)",
            fontSize: "0.78rem",
            lineHeight: 1.5,
            width: "max-content",
            maxWidth: 320,
            boxShadow: "var(--shadow-md)",
            zIndex: 1000,
            pointerEvents: "none",
            border: "1px solid var(--color-border)",
          }}
        >
          <strong style={{ color: "var(--color-accent)", display: "block", marginBottom: 2 }}>
            {entry?.term ?? term}
          </strong>
          {definition}
        </span>
      )}
    </span>
  );
}
