import js from "@eslint/js";
import prettier from "eslint-plugin-prettier/recommended";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  // Build output and vendored shadcn/ui components (generated — not linted).
  globalIgnores(["dist", "src/components/ui/**"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      prettier,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Treat a leading underscore as "intentionally unused".
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
  {
    // These modules intentionally co-locate a small helper/hook next to a
    // component. react-refresh's "export only components" rule is an HMR-only
    // hint, not a correctness concern, so it does not apply here.
    files: [
      "src/components/admin/memory-debug/shared.tsx",
      "src/components/layout/ThemeProvider.tsx",
      "src/components/scheduler/ScheduleBuilder.tsx",
      "src/components/sidebar/sidebar-chrome.tsx",
    ],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
  {
    // Standard data-loading / reset / timer effects that set state on mount or
    // when their inputs change — the intended pattern in these components.
    files: [
      "src/components/admin/memory-debug/FileContextPanel.tsx",
      "src/components/admin/memory-debug/RoutingDiagnosticsPanel.tsx",
      "src/components/chat/LiveTimer.tsx",
      "src/components/scheduler/TaskDetailsModal.tsx",
    ],
    rules: {
      "react-hooks/set-state-in-effect": "off",
    },
  },
]);
