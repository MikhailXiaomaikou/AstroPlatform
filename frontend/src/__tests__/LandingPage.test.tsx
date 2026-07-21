/**
 * Regression tests for the Landing page.
 *
 * M3 (2026-05) deleted the /search, /pipeline, /adql and /workspace pages;
 * App.tsx's catch-all route silently bounces unknown paths back to "/".
 * These tests pin that every clickable "In this issue" card targets a route
 * that still exists, so a card click never turns into a silent no-op.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { I18nProvider, useI18n, type Lang } from "../i18n";
import { TOC } from "../pages/Landing/LandingPage";
import { NAV_BY_PATH } from "../routes";

function HomeEyebrowProbe() {
  const { lang, setLang, t } = useI18n();
  return (
    <div>
      <span data-testid="language">{lang}</span>
      <span data-testid="home-eyebrow">{t("home.eyebrow")}</span>
      {(["en", "zh", "fr", "es"] as Lang[]).map((next) => (
        <button key={next} type="button" onClick={() => setLang(next)}>{next}</button>
      ))}
    </div>
  );
}

describe("LandingPage TOC cards", () => {
  it("every clickable 'In this issue' card points at a live route", () => {
    for (const card of TOC) {
      if (!card.to) continue;
      expect(
        NAV_BY_PATH[card.to],
        `card "${card.title}" links to ${card.to}, which is not a registered route`,
      ).toBeDefined();
    }
  });
});

describe("LandingPage evidence positioning", () => {
  it.each([
    ["en", "Observational cosmology focus · open research alpha · provenance first"],
    ["zh", "观测宇宙学专向 · 开放研究 Alpha · 来源记录优先"],
    ["fr", "Axé cosmologie observationnelle · alpha de recherche ouverte · traçabilité d'abord"],
    ["es", "Enfoque en cosmología observacional · alfa de investigación abierta · procedencia primero"],
  ] as const)("describes the product as a research alpha in %s", (language, expected) => {
    localStorage.setItem("astro_lang", "en");
    render(<I18nProvider><HomeEyebrowProbe /></I18nProvider>);
    fireEvent.click(screen.getByRole("button", { name: language }));
    expect(screen.getByTestId("language")).toHaveTextContent(language);
    expect(screen.getByTestId("home-eyebrow")).toHaveTextContent(expected);
  });
});
