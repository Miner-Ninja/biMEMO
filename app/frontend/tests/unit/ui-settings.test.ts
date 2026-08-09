/**
 * Unit tests for the UI settings write-through store.
 *
 * Asserts the ui-settings-db contract: R3 (S3.1-S3.3), R4 (S4.1-S4.2),
 * R5 (S5.1).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Node's experimental bare `localStorage` global shadows jsdom's — give
// the module and the tests one real in-memory implementation.
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => (key in store ? store[key] : null),
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();
Object.defineProperty(window, "localStorage", { value: localStorageMock });
Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
});

import { getUiSetting, setUiSetting, syncUiSettings } from "@/lib/ui-settings";
import { apiRequest } from "@/lib/bimemo-api";

vi.mock("@/lib/bimemo-api", () => ({
  apiRequest: vi.fn(),
}));

const apiRequestMock = vi.mocked(apiRequest);

describe("ui-settings store", () => {
  beforeEach(() => {
    localStorage.clear();
    apiRequestMock.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("S3.1 — reads apply from the cache without any network call", () => {
    localStorage.setItem("bm-ui-settings", JSON.stringify({ theme: "dark" }));
    expect(getUiSetting("theme")).toBe("dark");
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it("S3.2 — server values overwrite the cache on sync", async () => {
    localStorage.setItem("bm-ui-settings", JSON.stringify({ theme: "dark" }));
    apiRequestMock.mockResolvedValueOnce({ settings: { theme: "light" } });
    await syncUiSettings();
    expect(getUiSetting("theme")).toBe("light");
  });

  it("S3.3 — a failed push keeps the new value in the cache", async () => {
    apiRequestMock.mockRejectedValue(new Error("server down"));
    setUiSetting("voice_mode", true);
    await vi.runAllTimersAsync();
    expect(getUiSetting("voice_mode")).toBe(true);
  });

  it("S4.1 — empty server triggers legacy migration and cleanup", async () => {
    localStorage.setItem("theme", "dark");
    localStorage.setItem("bm-voice-mode", "true");
    localStorage.setItem("commandHistory", JSON.stringify(["ls", "htop"]));
    apiRequestMock.mockResolvedValueOnce({ settings: {} });
    apiRequestMock.mockResolvedValueOnce({ ok: true });
    await syncUiSettings();
    expect(getUiSetting("theme")).toBe("dark");
    expect(getUiSetting("voice_mode")).toBe(true);
    expect(getUiSetting("command_history")).toEqual(["ls", "htop"]);
    expect(localStorage.getItem("theme")).toBeNull();
    expect(localStorage.getItem("commandHistory")).toBeNull();
    expect(localStorage.getItem("bm-voice-mode")).toBeNull();
    const putCall = apiRequestMock.mock.calls.find(
      ([, options]) => options && (options as RequestInit).method === "PUT"
    );
    expect(putCall).toBeDefined();
  });

  it("S4.2 — non-empty server ignores legacy keys and removes them", async () => {
    localStorage.setItem("theme", "dark");
    apiRequestMock.mockResolvedValueOnce({ settings: { theme: "light" } });
    await syncUiSettings();
    expect(getUiSetting("theme")).toBe("light");
    expect(localStorage.getItem("theme")).toBeNull();
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
  });

  it("S5.1 — command history is capped at the newest 50 entries", () => {
    apiRequestMock.mockResolvedValue({ ok: true });
    const commands = Array.from({ length: 60 }, (_, i) => `cmd-${i}`);
    setUiSetting("command_history", commands);
    const stored = getUiSetting("command_history");
    expect(stored).toHaveLength(50);
    expect(stored?.[0]).toBe("cmd-10");
    expect(stored?.[49]).toBe("cmd-59");
  });
});
