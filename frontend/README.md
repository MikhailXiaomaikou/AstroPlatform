# Standard Astro Frontend

React 19 + TypeScript + Vite frontend for the Standard Astro research platform.

## Commands

```bash
npm install
npm run dev      # Vite dev server on :5173
npm test         # Vitest
npm run build    # tsc -b && vite build
npm run lint     # ESLint
```

`npm run build` is the production gate. TypeScript is strict: `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, and `erasableSyntaxOnly` are enabled, so type-only imports must use `import type`.

## Runtime Configuration

```bash
VITE_API_URL=https://your-backend.example
VITE_GOOGLE_CLIENT_ID=...
```

When `VITE_API_URL` is omitted, the API client defaults to `http://localhost:8000`.

## Key Areas

- `src/App.tsx` — Journal masthead navigation, backend wake-up banner, theme/language shell.
- `src/pages/Chat/ChatPage.tsx` — AI assistant SSE loop, action cards, provenance panels, honest abstention, maintenance-gated tool rendering.
- `src/components/chat/DataSourcesPanel.tsx` — Per-tool-result provenance display: service, `archive_version`, ivoid, article/bibcode, source authority, field-bibcode counts.
- `src/components/chat/AckButton.tsx` — Clipboard acknowledgement generator from conversation provenance.
- `src/hooks/useConversationProvenance.ts` — Aggregates and dedupes provenance across chat turns.
- `src/pages/DataBrowser/*` — Multi-source search UI. Non-v2 sources render as under-maintenance chips during the provenance-v2 rollout.
- `src/api/client.ts` — Axios + SSE client with one-shot Render cold-start retry.

## Provenance-v2 UI Rules

- Active data sources are VizieR, Gaia DR3, SIMBAD, NED, and 2MASS.
- Gated sources such as SDSS, Chandra, JWST, MAST, and radio catalogs must display as **Maintenance** / `UNAVAILABLE`, not generic FAILED or EMPTY states.
- Tool cards may show Data Sources and Copy Acknowledgement controls when nested `provenance` is present.
- Do not redesign the existing SYNTHETIC, FAILED, or EMPTY semantics when changing provenance UI.

## Tests

The current frontend suite is 148 Vitest cases, including ChatPage, DataSourcesPanel, AckButton, SearchBar maintenance-gating, visualization components, and common utilities.
