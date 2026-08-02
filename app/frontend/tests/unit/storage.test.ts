import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  STORAGE_KEYS,
  saveTheme,
  loadTheme,
  getInitialTheme,
  saveAuthUser,
  clearAuth,
} from "@/lib/storage";

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
  };
})();

Object.defineProperty(window, "localStorage", { value: localStorageMock });

// Mock matchMedia
Object.defineProperty(window, "matchMedia", {
  value: vi.fn().mockImplementation((query) => ({
    matches: query === "(prefers-color-scheme: dark)",
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

describe("STORAGE_KEYS", () => {
  it("has all required keys", () => {
    expect(STORAGE_KEYS.THEME).toBe("theme");
    expect(STORAGE_KEYS.AUTH_USER).toBe("authUser");
  });
});

describe("saveTheme / loadTheme", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  it("saves and loads dark theme", () => {
    saveTheme("dark");
    expect(loadTheme()).toBe("dark");
  });

  it("saves and loads light theme", () => {
    saveTheme("light");
    expect(loadTheme()).toBe("light");
  });

  it("returns null for invalid theme", () => {
    localStorageMock.setItem(STORAGE_KEYS.THEME, "invalid");
    expect(loadTheme()).toBeNull();
  });

  it("returns null when no theme saved", () => {
    expect(loadTheme()).toBeNull();
  });
});

describe("getInitialTheme", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  it("returns stored theme if available", () => {
    saveTheme("light");
    expect(getInitialTheme()).toBe("light");
  });

  it("returns dark if system prefers dark", () => {
    // matchMedia mock returns true for dark scheme
    expect(getInitialTheme()).toBe("dark");
  });
});

describe("saveAuthUser / clearAuth", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  it("saves auth user", () => {
    saveAuthUser("john");
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      STORAGE_KEYS.AUTH_USER,
      "john"
    );
  });

  it("clears auth data", () => {
    saveAuthUser("john");
    clearAuth();
    expect(localStorageMock.removeItem).toHaveBeenCalledWith(STORAGE_KEYS.AUTH_USER);
  });
});
