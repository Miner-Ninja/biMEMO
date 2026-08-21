import { useState, useEffect, useCallback, useRef } from "react";
import type { Theme } from "@/features/chat/model/chat";
import { getUiSetting, setUiSetting } from "@/utils/ui-settings";

const FADE_DURATION_MS = 200;

function getInitialTheme(): Theme {
  const stored = getUiSetting("theme");
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Hook for managing theme state persisted via the UI settings store
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const isInitialMount = useRef(true);

  // Apply theme to document
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      document.documentElement.classList.remove("dark", "light");
      document.documentElement.classList.add(theme);
      setUiSetting("theme", theme);
      return;
    }

    document.documentElement.style.opacity = "0";

    const timer = setTimeout(() => {
      document.documentElement.classList.remove("dark", "light");
      document.documentElement.classList.add(theme);
      setUiSetting("theme", theme);
      document.documentElement.style.opacity = "1";
    }, FADE_DURATION_MS);

    return () => clearTimeout(timer);
  }, [theme]);

  // Toggle between dark and light
  const toggleTheme = useCallback(() => {
    setThemeState((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  // Set a specific theme
  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme);
  }, []);

  return {
    theme,
    toggleTheme,
    setTheme,
    isDark: theme === "dark",
  };
}
