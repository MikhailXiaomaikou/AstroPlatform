// Single source of truth for navigation routes.
//
// History: M3 (2026-05) deleted /search, /pipeline, /adql, /workspace pages.
// Before this constant, CommandPalette.tsx and OnboardingOverlay.tsx kept
// their own static arrays referencing the deleted routes — Cmd+K landed on
// 404, and the onboarding tour silently failed on 3 of its 4 steps because
// document.querySelector('a[href="/pipeline"]') returned null.
//
// All UI surfaces that enumerate routes (command palette, onboarding tour,
// future site map) MUST import NAV_ROUTES from here. App.tsx remains the
// source of truth for the actual <Route> definitions; this constant lists
// the subset that should be discoverable via search/tour.

export type NavCategory = "primary" | "secondary";

export interface NavRoute {
  id: string;
  path: string;
  labelKey: string;
  descKey?: string;
  categoryKey: string;
  category: NavCategory;
  keywords?: string;
  /** Selector used by the onboarding tour to highlight a nav link. */
  tourSelector?: string;
}

export const NAV_ROUTES: readonly NavRoute[] = [
  // Primary destinations — surfaced in main masthead nav and the onboarding tour.
  {
    id: "nav-chat",
    path: "/chat",
    labelKey: "cmd.ai_assistant",
    descKey: "onboard.chat_desc",
    categoryKey: "cmd.cat_nav",
    category: "primary",
    keywords: "ask question chat assistant",
    tourSelector: 'a[href="/chat"]',
  },
  {
    id: "nav-papers",
    path: "/papers",
    labelKey: "cmd.papers",
    descKey: "onboard.papers_desc",
    categoryKey: "cmd.cat_nav",
    category: "primary",
    keywords: "papers latex drafts share",
    tourSelector: 'a[href="/papers"]',
  },
  {
    id: "nav-account",
    path: "/account",
    labelKey: "cmd.account",
    descKey: "onboard.account_desc",
    categoryKey: "cmd.cat_nav",
    category: "primary",
    keywords: "config preferences settings research profile api keys",
    tourSelector: 'a[href="/account"]',
  },

  // Secondary destinations — reachable via Cmd+K and footer links but not
  // featured in the onboarding tour to keep it concise.
  {
    id: "nav-team",
    path: "/team",
    labelKey: "cmd.team",
    categoryKey: "cmd.cat_nav",
    category: "secondary",
    keywords: "collaborate share team",
  },
  {
    id: "nav-observations",
    path: "/observations",
    labelKey: "cmd.observations",
    categoryKey: "cmd.cat_nav",
    category: "secondary",
    keywords: "transient supernova alert anomaly observations",
  },
  {
    id: "nav-help",
    path: "/help",
    labelKey: "cmd.help",
    categoryKey: "cmd.cat_nav",
    category: "secondary",
    keywords: "documentation guide help",
  },
];

/** Routes the onboarding tour should highlight, in order. */
export const TOUR_ROUTES: readonly NavRoute[] = NAV_ROUTES.filter(
  (r) => r.category === "primary" && Boolean(r.tourSelector),
);

/** Path → NavRoute lookup. */
export const NAV_BY_PATH: Readonly<Record<string, NavRoute>> = Object.freeze(
  Object.fromEntries(NAV_ROUTES.map((r) => [r.path, r])),
);
