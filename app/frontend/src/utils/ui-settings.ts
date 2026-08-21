/**
 * Web-tier UI settings with a write-through cache.
 *
 * The server blob (PUT/GET /admin/settings/ui) is the source of truth;
 * localStorage under one key is a synchronous cache so the UI applies
 * settings before any network round trip. Every change lands in the
 * cache immediately and is pushed to the server debounced. A failed
 * push keeps the cache working (degraded, logged).
 */

import { ApiError, apiRequest } from "@/services/bimemo-api";

export interface UiSettings {
  theme?: "dark" | "light";
  voice_mode?: boolean;
  ui_language?: string;
  command_history?: string[];
  /** ISO timestamp of the last local write; freshness for sync. */
  updated_at?: string;
}

const CACHE_KEY = "bm-ui-settings";
export const COMMAND_HISTORY_LIMIT = 50;
const COMMAND_HISTORY_ENTRY_MAX_CHARS = 500;
const PUSH_DEBOUNCE_MS = 500;

// Legacy per-key storage migrated into the blob on first sync.
const LEGACY_THEME_KEY = "theme";
const LEGACY_HISTORY_KEY = "commandHistory";
const LEGACY_VOICE_KEY = "bm-voice-mode";

let pushTimer: ReturnType<typeof setTimeout> | null = null;

function isSettingsObject(value: unknown): value is UiSettings {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readCache(): UiSettings {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return isSettingsObject(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeCache(settings: UiSettings): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(settings));
  } catch (error) {
    console.error("Failed to cache UI settings:", error);
  }
}

function readLegacySettings(): UiSettings {
  const legacy: UiSettings = {};
  try {
    const theme = localStorage.getItem(LEGACY_THEME_KEY);
    if (theme === "dark" || theme === "light") legacy.theme = theme;
    const voice = localStorage.getItem(LEGACY_VOICE_KEY);
    if (voice !== null) legacy.voice_mode = voice === "true";
    const history = localStorage.getItem(LEGACY_HISTORY_KEY);
    if (history) {
      const parsed: unknown = JSON.parse(history);
      if (Array.isArray(parsed)) {
        legacy.command_history = parsed
          .filter((item): item is string => typeof item === "string")
          .slice(-COMMAND_HISTORY_LIMIT);
      }
    }
  } catch {
    // Unreadable legacy values are simply skipped.
  }
  return legacy;
}

function removeLegacyKeys(): void {
  try {
    localStorage.removeItem(LEGACY_THEME_KEY);
    localStorage.removeItem(LEGACY_HISTORY_KEY);
    localStorage.removeItem(LEGACY_VOICE_KEY);
  } catch {
    // Best effort.
  }
}

async function pushToServer(
  settings: UiSettings,
  options: { keepalive?: boolean } = {}
): Promise<void> {
  await apiRequest("/admin/settings/ui", {
    method: "PUT",
    body: JSON.stringify({ settings }),
    keepalive: options.keepalive ?? false,
  });
}

/** Push a pending debounced change immediately (used on page hide). */
function flushPendingPush(): void {
  if (pushTimer === null) return;
  clearTimeout(pushTimer);
  pushTimer = null;
  pushToServer(readCache(), { keepalive: true }).catch((error) => {
    console.warn("UI settings flush failed; cache stays local:", error);
  });
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushPendingPush();
  });
  window.addEventListener("pagehide", flushPendingPush);
}

async function pushWithCapRecovery(settings: UiSettings): Promise<void> {
  try {
    await pushToServer(settings);
  } catch (error) {
    if (error instanceof ApiError && error.status === 413) {
      // Preferences matter more than input hints: drop the history and
      // retry once so settings sync never stays broken.
      const slim: UiSettings = { ...settings };
      delete slim.command_history;
      writeCache(slim);
      await pushToServer(slim);
      return;
    }
    throw error;
  }
}

function schedulePush(): void {
  if (pushTimer !== null) clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    pushTimer = null;
    pushWithCapRecovery(readCache()).catch((error) => {
      console.warn("UI settings push failed; cache stays local:", error);
    });
  }, PUSH_DEBOUNCE_MS);
}

/** Read one setting synchronously from the cache. */
export function getUiSetting<K extends keyof UiSettings>(key: K): UiSettings[K] {
  return readCache()[key];
}

/** Write one setting: cache immediately, server debounced. */
export function setUiSetting<K extends keyof UiSettings>(
  key: K,
  value: UiSettings[K]
): void {
  const settings = readCache();
  if (key === "command_history" && Array.isArray(value)) {
    // Newest-first list; entries trimmed so one long message can never
    // blow the server-side blob cap.
    settings[key] = value
      .map((entry) => entry.slice(0, COMMAND_HISTORY_ENTRY_MAX_CHARS))
      .slice(0, COMMAND_HISTORY_LIMIT) as UiSettings[K];
  } else {
    settings[key] = value;
  }
  settings.updated_at = new Date().toISOString();
  writeCache(settings);
  schedulePush();
}

/**
 * Sync with the server after login.
 *
 * Server values win over the cache. An empty server blob triggers the
 * one-time migration: current cache merged with legacy per-key values
 * is pushed, and the legacy keys are removed.
 */
export async function syncUiSettings(): Promise<void> {
  let server: UiSettings;
  try {
    const response = await apiRequest<{ settings: UiSettings }>("/admin/settings/ui");
    server = response.settings ?? {};
  } catch (error) {
    console.warn("UI settings sync skipped (server unreachable):", error);
    return;
  }
  if (Object.keys(server).length > 0) {
    const cached = readCache();
    const cacheIsFresher =
      typeof cached.updated_at === "string" &&
      (typeof server.updated_at !== "string" || cached.updated_at > server.updated_at);
    if (cacheIsFresher) {
      // The server missed our last writes (e.g. it was unreachable at
      // push time) — bring it up to date instead of reverting the user.
      try {
        await pushWithCapRecovery(cached);
      } catch (error) {
        console.warn("UI settings catch-up push failed:", error);
      }
    } else {
      writeCache(server);
    }
    removeLegacyKeys();
    return;
  }
  const seed: UiSettings = { ...readLegacySettings(), ...readCache() };
  if (Object.keys(seed).length === 0) return;
  seed.updated_at = new Date().toISOString();
  writeCache(seed);
  try {
    await pushWithCapRecovery(seed);
    removeLegacyKeys();
  } catch (error) {
    console.warn("UI settings migration push failed:", error);
  }
}
