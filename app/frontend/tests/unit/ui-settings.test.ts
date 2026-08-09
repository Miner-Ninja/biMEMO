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

vi.mock("@/lib/bimemo-api", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }
  return { apiRequest: vi.fn(), ApiError };
});

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

  it("S5.1 — command history keeps the newest 50, entries trimmed", () => {
    apiRequestMock.mockResolvedValue({ ok: true });
    // Newest-first convention: index 0 is the most recent command.
    const commands = Array.from({ length: 60 }, (_, i) => `cmd-${i}`);
    commands[0] = "y".repeat(2000);
    setUiSetting("command_history", commands);
    const stored = getUiSetting("command_history");
    expect(stored).toHaveLength(50);
    expect(stored?.[0]).toBe("y".repeat(500));
    expect(stored?.[1]).toBe("cmd-1");
    expect(stored?.[49]).toBe("cmd-49");
  });

  it("S5.2 — a 413 push drops the history and retries once", async () => {
    const { ApiError } = await import("@/lib/bimemo-api");
    apiRequestMock
      .mockRejectedValueOnce(new ApiError(413, "blob exceeds cap"))
      .mockResolvedValueOnce({ ok: true });
    setUiSetting("command_history", ["one", "two"]);
    setUiSetting("theme", "dark");
    await vi.runAllTimersAsync();
    expect(getUiSetting("command_history")).toBeUndefined();
    expect(getUiSetting("theme")).toBe("dark");
    const putBodies = apiRequestMock.mock.calls
      .filter(([, options]) => (options as RequestInit)?.method === "PUT")
      .map(([, options]) => String((options as RequestInit).body));
    expect(putBodies).toHaveLength(2);
    expect(putBodies[1]).not.toContain("command_history");
  });

  it("S3.4 — a fresher cache is pushed instead of being reverted", async () => {
    localStorage.setItem(
      "bm-ui-settings",
      JSON.stringify({ theme: "dark", updated_at: "2026-08-09T10:00:00Z" })
    );
    apiRequestMock.mockResolvedValueOnce({
      settings: { theme: "light", updated_at: "2026-08-09T09:00:00Z" },
    });
    apiRequestMock.mockResolvedValueOnce({ ok: true });
    await syncUiSettings();
    expect(getUiSetting("theme")).toBe("dark");
    const putCall = apiRequestMock.mock.calls.find(
      ([, options]) => (options as RequestInit)?.method === "PUT"
    );
    expect(putCall).toBeDefined();
  });
});
