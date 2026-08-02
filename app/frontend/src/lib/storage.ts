/**
 * localStorage keys for persistence
 */
export const STORAGE_KEYS = {
  THEME: "theme",
  AUTH_USER: "authUser",
  COMMAND_HISTORY: "commandHistory",
} as const;

/**
 * Save theme to localStorage
 */
export function saveTheme(theme: "dark" | "light"): void {
  try {
    localStorage.setItem(STORAGE_KEYS.THEME, theme);
  } catch (error) {
    console.error("Failed to save theme:", error);
  }
}

/**
 * Load theme from localStorage
 */
export function loadTheme(): "dark" | "light" | null {
  try {
    const theme = localStorage.getItem(STORAGE_KEYS.THEME);
    if (theme === "dark" || theme === "light") return theme;
    return null;
  } catch {
    return null;
  }
}

/**
 * Get initial theme based on localStorage or system preference
 */
export function getInitialTheme(): "dark" | "light" {
  const stored = loadTheme();
  if (stored) return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Save auth user to localStorage (for display purposes only, not for auth)
 */
export function saveAuthUser(username: string): void {
  try {
    localStorage.setItem(STORAGE_KEYS.AUTH_USER, username);
  } catch (error) {
    console.error("Failed to save auth user:", error);
  }
}

/**
 * Save command history to localStorage
 */
export function saveCommandHistory(history: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEYS.COMMAND_HISTORY, JSON.stringify(history));
  } catch (error) {
    console.error("Failed to save command history:", error);
  }
}

/**
 * Load command history from localStorage
 */
export function loadCommandHistory(): string[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEYS.COMMAND_HISTORY);
    if (!saved) return [];
    const parsed = JSON.parse(saved);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/**
 * Clear auth data from localStorage
 */
export function clearAuth(): void {
  try {
    localStorage.removeItem(STORAGE_KEYS.AUTH_USER);
  } catch (error) {
    console.error("Failed to clear auth:", error);
  }
}
