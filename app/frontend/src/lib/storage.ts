/**
 * localStorage keys for persistence
 */
export const STORAGE_KEYS = {
  AUTH_USER: "authUser",
} as const;

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
 * Clear auth data from localStorage
 */
export function clearAuth(): void {
  try {
    localStorage.removeItem(STORAGE_KEYS.AUTH_USER);
  } catch (error) {
    console.error("Failed to clear auth:", error);
  }
}
