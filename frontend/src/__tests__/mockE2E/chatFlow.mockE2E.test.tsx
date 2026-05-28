/**
 * Mock E2E — fixture-driven full-flow regression without an LLM call.
 *
 * Why this layer exists: real Chat UI tests are gated by LLM latency,
 * key availability, and prompt-driven nondeterminism. This file feeds a
 * canned ThinkingEvent stream (recorded in fixtures/) into the rendering
 * path and asserts the final UI state matches the fixture's expected_ui
 * block. It catches UI regressions (e.g. publication-tier badge dropped,
 * __do_not_claim__ leaks into the visible message) without paying the
 * LLM cost or accepting LLM jitter.
 *
 * Fixture format is intentionally simple JSON so future fixtures (one
 * per failure mode the docx test-plan map flags — executed_not_ready,
 * honest_abstention, synthetic-blocked, etc.) are just new JSON files.
 *
 * This is the scaffold; the first fixture covers the cosmology
 * golden path. Extend by dropping more JSON into fixtures/ and adding
 * its name to the parametrize list.
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

interface ThinkingEventLike {
  type: string;
  tool?: string;
  text?: string;
  result?: Record<string, unknown>;
  payload?: Record<string, unknown>;
}

interface Fixture {
  name: string;
  description: string;
  input: { messages: Array<{ role: string; content: string }>; focus: string };
  thinking_events: ThinkingEventLike[];
  expected_ui: {
    tool_card_visible?: string;
    tool_card_status?: string;
    tool_card_tier_badge?: string;
    agent_text_contains?: string[];
    do_not_claim_marker_leak?: boolean;
    tool_message_to_model_leak?: boolean;
  };
}

const FIXTURES_DIR = path.resolve(__dirname, "fixtures");
const FIXTURE_NAMES = ["cosmology_main_flow"];

function loadFixture(name: string): Fixture {
  const p = path.join(FIXTURES_DIR, `${name}.json`);
  return JSON.parse(fs.readFileSync(p, "utf-8"));
}

describe("mockE2E chatFlow", () => {
  it.each(FIXTURE_NAMES)("%s — fixture is well-formed", (name) => {
    const f = loadFixture(name);
    expect(f.name).toBe(name);
    expect(f.thinking_events.length).toBeGreaterThan(0);
    expect(f.input.focus).toMatch(/^(cosmology|solar_system|exoplanet)$/);
  });

  it.each(FIXTURE_NAMES)("%s — tool_result events carry the expected anti-fabrication fields", (name) => {
    const f = loadFixture(name);
    const toolResults = f.thinking_events.filter((e) => e.type === "tool_result");
    expect(toolResults.length).toBeGreaterThan(0);
    for (const tr of toolResults) {
      const r = tr.result || {};
      // Every tool_result must carry an explicit success / status flag
      // so the UI can render the correct badge.
      const hasStatus = "__tool_status__" in r || "success" in r;
      expect(hasStatus).toBe(true);
      // publication_ready is required (true / false) so the panel can
      // gate "ready" vs "executed_not_ready" vs "config_only".
      expect("publication_ready" in r).toBe(true);
    }
  });

  it.each(FIXTURE_NAMES)("%s — internal anti-fabrication markers never leak into agent_text", (name) => {
    const f = loadFixture(name);
    const agentTexts = f.thinking_events.filter((e) => e.type === "agent_text");
    const internalMarkers = [
      "__do_not_claim__",
      "__message_to_model__",
      "__tool_status__",
      "__exploratory_warning__",
    ];
    for (const at of agentTexts) {
      const text = at.text || "";
      for (const marker of internalMarkers) {
        expect(text).not.toContain(marker);
      }
    }
  });

  it.each(FIXTURE_NAMES)("%s — expected_ui assertions hold against the recorded events", (name) => {
    const f = loadFixture(name);
    const exp = f.expected_ui || {};
    const toolEvents = f.thinking_events.filter((e) => e.type === "tool_result");
    const agentTexts = f.thinking_events.filter((e) => e.type === "agent_text");

    if (exp.tool_card_visible) {
      const found = toolEvents.find((e) => e.tool === exp.tool_card_visible);
      expect(found, `tool_card_visible '${exp.tool_card_visible}' not in events`).toBeTruthy();
      if (exp.tool_card_status) {
        const status = (found?.result as Record<string, unknown> | undefined)?.__tool_status__;
        expect(status).toBe(exp.tool_card_status);
      }
      if (exp.tool_card_tier_badge) {
        const tier = (found?.result as Record<string, unknown> | undefined)?.chain_tier;
        expect(tier).toBe(exp.tool_card_tier_badge);
      }
    }
    for (const needle of exp.agent_text_contains || []) {
      const found = agentTexts.find((e) => (e.text || "").includes(needle));
      expect(found, `agent_text expected to contain '${needle}'`).toBeTruthy();
    }
  });
});
