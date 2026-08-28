# biMEMO frontend

React and TypeScript frontend for the biMEMO self-hosted AI assistant. It
provides chat, authentication and first-run setup, file and voice input, and
the administration interface for LLM providers, integrations, memory,
scheduled tasks, skills, backups, and diagnostics.

For product setup, start with the [project README](../../README.md). This file
is for frontend development.

## Requirements

- Node.js 22
- npm
- A running biMEMO backend for live API and chat flows

## Local development

```bash
npm ci
npm run dev
```

Vite serves the frontend at <http://localhost:5173>. In the normal development
stack, Docker exposes it at <http://localhost:5273> and Caddy serves the full
application at <https://localhost>.

The Vite development proxy reads `BIMEMO_DEV_PROXY_TARGET`; the Compose stack
sets it to `http://bimemo:8000`.

## Checks

```bash
npm run lint
npm run typecheck
npm run build
npm run test
```

Browser-level tests require a running full stack. See
[tests/e2e/README.md](tests/e2e/README.md) before running them, especially the
destructive first-run scenario.

## Main areas

```text
src/
├── components/        shared layout, sidebar, icons, and UI primitives
├── features/          chat, auth, providers, integrations, memory, scheduler…
├── hooks/             shared React hooks and realtime server state
├── i18n/              English and Russian translations
├── services/          API and query-client setup
└── utils/             file-upload and UI helpers
```

## Stack

React 19, TypeScript 5.9, Vite 7, Tailwind CSS 4, shadcn, TanStack Query,
React Router, i18next, Zod, Vitest, and Playwright.
