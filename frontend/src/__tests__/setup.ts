/**
 * Global test setup for component tests.
 *
 * This file is loaded by Vitest via the setupFiles configuration in
 * vite.config.ts. It extends Vitest matchers with jest-dom and provides
 * browser API mocks required by Plotly / React components.
 */
import "@testing-library/jest-dom/vitest";

// Mock ResizeObserver (needed by Plotly / reactflow)
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver =
  ResizeObserverMock as unknown as typeof ResizeObserver;

// Mock matchMedia (needed by various UI components)
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
