import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { I18nProvider, useI18n, type Lang } from "../i18n";

function TranslationProbe() {
  const { lang, setLang, t } = useI18n();
  return (
    <div>
      <span data-testid="language">{lang}</span>
      <span data-testid="bot-label">{t("nav.research_bot")}</span>
      {(["en", "zh", "fr", "es"] as Lang[]).map((next) => (
        <button key={next} type="button" onClick={() => setLang(next)}>{next}</button>
      ))}
    </div>
  );
}

describe("Research Bot translations", () => {
  beforeEach(() => localStorage.setItem("astro_lang", "en"));

  it.each([
    ["en", "Research Bot"],
    ["zh", "研究 Bot"],
    ["fr", "Bot de recherche"],
    ["es", "Bot de investigación"],
  ] as const)("provides the navigation label in %s", (language, expected) => {
    render(<I18nProvider><TranslationProbe /></I18nProvider>);
    fireEvent.click(screen.getByRole("button", { name: language }));
    expect(screen.getByTestId("language")).toHaveTextContent(language);
    expect(screen.getByTestId("bot-label")).toHaveTextContent(expected);
  });
});
