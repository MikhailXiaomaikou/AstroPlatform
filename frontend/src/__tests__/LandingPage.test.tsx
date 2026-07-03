/**
 * Regression tests for the Landing page.
 *
 * M3 (2026-05) deleted the /search, /pipeline, /adql and /workspace pages;
 * App.tsx's catch-all route silently bounces unknown paths back to "/".
 * These tests pin that every clickable "In this issue" card targets a route
 * that still exists, so a card click never turns into a silent no-op.
 */
import { describe, it, expect } from "vitest";
import { TOC } from "../pages/Landing/LandingPage";
import { NAV_BY_PATH } from "../routes";

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
