/**
 * Unit tests for the interface-language core.
 *
 * Asserts the ui-i18n contract: R2 (S2.1-S2.3), R3 (S3.2 persist,
 * S3.3 apply stored), R4 (S4.2 Russian plural forms) and dictionary
 * key parity between en and ru (R1/S1.3 runtime complement).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/ui-settings", () => ({
  getUiSetting: vi.fn(),
  setUiSetting: vi.fn(),
}));

import i18n, {
  applyStoredLanguage,
  resolveInitialLanguage,
  setUiLanguage,
} from "@/i18n";
import { getUiSetting, setUiSetting } from "@/lib/ui-settings";
import en from "@/i18n/locales/en";
import ru from "@/i18n/locales/ru";

const getUiSettingMock = vi.mocked(getUiSetting);
const setUiSettingMock = vi.mocked(setUiSetting);

function stubBrowserLanguage(value: string): void {
  Object.defineProperty(window.navigator, "language", {
    get: () => value,
    configurable: true,
  });
}

describe("interface language core", () => {
  beforeEach(() => {
    getUiSettingMock.mockReset();
    setUiSettingMock.mockReset();
  });

  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("S2.1 — no stored choice, Russian browser → Russian", () => {
    getUiSettingMock.mockReturnValue(undefined);
    stubBrowserLanguage("ru-RU");
    expect(resolveInitialLanguage()).toBe("ru");
  });

  it("S2.2 — no stored choice, non-Russian browser → English", () => {
    getUiSettingMock.mockReturnValue(undefined);
    stubBrowserLanguage("de-DE");
    expect(resolveInitialLanguage()).toBe("en");
  });

  it("S2.3 — stored choice wins over the browser language", () => {
    getUiSettingMock.mockReturnValue("ru");
    stubBrowserLanguage("en-US");
    expect(resolveInitialLanguage()).toBe("ru");
  });

  it("S3.2 — manual switch applies and persists under ui_language", () => {
    setUiLanguage("ru");
    expect(i18n.language).toBe("ru");
    expect(setUiSettingMock).toHaveBeenCalledWith("ui_language", "ru");
  });

  it("S3.3 — a stored choice from another browser is applied on sync", () => {
    getUiSettingMock.mockReturnValue("ru");
    applyStoredLanguage();
    expect(i18n.language).toBe("ru");
  });

  it("S4.2 — Russian plural forms for interval descriptions", async () => {
    await i18n.changeLanguage("ru");
    expect(i18n.t("schedule.everyMinute", { count: 1 })).toBe("Каждую 1 минуту");
    expect(i18n.t("schedule.everyMinute", { count: 2 })).toBe("Каждые 2 минуты");
    expect(i18n.t("schedule.everyMinute", { count: 5 })).toBe("Каждые 5 минут");
    await i18n.changeLanguage("en");
    expect(i18n.t("schedule.everyMinute", { count: 1 })).toBe("Every 1 minute");
    expect(i18n.t("schedule.everyMinute", { count: 5 })).toBe("Every 5 minutes");
  });

  it("dictionary key parity — ru mirrors the en shape exactly", () => {
    const flatten = (obj: Record<string, unknown>, prefix = ""): string[] =>
      Object.entries(obj).flatMap(([key, value]) =>
        typeof value === "object" && value !== null
          ? flatten(value as Record<string, unknown>, `${prefix}${key}.`)
          : [`${prefix}${key}`]
      );
    expect(flatten(ru).sort()).toEqual(flatten(en).sort());
  });
});
