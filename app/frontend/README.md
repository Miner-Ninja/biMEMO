# AI SQL Assistant - Frontend

React-based chat interface for the AI SQL Assistant.

## Prerequisites

- Node.js 18+
- npm or yarn

## Setup

1. Install dependencies:

```bash
npm install
```

2. Create `.env.local` with MCP server URL:

```env
VITE_MCP_SERVER_URL=http://localhost:8001/mcp
```

3. Start development server:

```bash
npm run dev
```

4. Open http://localhost:5173

## Build

```bash
npm run build
```

Output will be in `dist/` directory.

## Features

- Chat with AI about your database
- SQL syntax highlighting with shiki
- Copy SQL queries to clipboard
- Export results to CSV
- Attach files (CSV, JSON, TXT, Excel)
- Dark/Light theme toggle
- Chat history with local storage persistence
- Mobile-responsive design

## Tech Stack

- React 18 + TypeScript
- Vite
- Tailwind CSS v4
- shadcn/ui components
- shiki for syntax highlighting
- Zod for validation

## Project Structure

```
src/
├── components/
│   ├── chat/       # Chat-related components
│   ├── layout/     # Layout components (Header, Layout, etc.)
│   ├── sidebar/    # Sidebar components
│   └── ui/         # shadcn/ui components
├── hooks/          # React hooks
├── lib/            # Utilities and MCP client
└── types/          # TypeScript types
```
