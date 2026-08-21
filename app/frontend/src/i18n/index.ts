/**
 * Interface language: i18next initialisation and language switching.
 *
 * Resolution order: the user's stored manual choice (ui_settings blob,
 * synchronous cache) wins; otherwise a Russian browser gets Russian and
 * everything else gets English. Dictionaries are bundled statically —
 * no network fetch, the language is ready before first render.
 */
import i18n from "i18next";
import type { ParseKeys } from "i18next";
import { initReactI18next } from "react-i18next";
import { enUS as dateFnsEn, ru as dateFnsRu } from "date-fns/locale";
import type { Locale } from "date-fns";
import { getUiSetting, setUiSetting } from "@/utils/ui-settings";
import en from "./locales/en";
import ru from "./locales/ru";

export const SUPPORTED_LANGUAGES = ["en", "ru"] as const;
export type UiLanguage = (typeof SUPPORTED_LANGUAGES)[number];

/** Union of all valid dictionary keys — for key maps resolved at runtime. */
export type TranslationKey = ParseKeys;

export function isUiLanguage(value: unknown): value is UiLanguage {
  return (SUPPORTED_LANGUAGES as readonly unknown[]).includes(value);
}

export function resolveInitialLanguage(): UiLanguage {
  const stored = getUiSetting("ui_language");
  if (isUiLanguage(stored)) return stored;
  const browser = typeof navigator !== "undefined" ? navigator.language : "";
  return browser.toLowerCase().startsWith("ru") ? "ru" : "en";
}

// initAsync: false makes init synchronous (static resources only), so
// t() works from the very first render. The guard keeps a test-setup
// initialisation (which must not pull the settings/api chain) intact.
if (!i18n.isInitialized) {
  void i18n.use(initReactI18next).init({
    resources: {
      en: { translation: en },
      ru: { translation: ru },
    },
    lng: resolveInitialLanguage(),
    fallbackLng: "en",
    interpolation: { escapeValue: false },
    initAsync: false,
  });
}

/** Manual switch: apply immediately, persist via the settings store. */
export function setUiLanguage(language: UiLanguage): void {
  void i18n.changeLanguage(language);
  setUiSetting("ui_language", language);
}

/**
 * Apply the stored choice after the post-login settings sync — picks
 * up a language selected from another browser.
 */
export function applyStoredLanguage(): void {
  const stored = getUiSetting("ui_language");
  if (isUiLanguage(stored) && stored !== i18n.language) {
    void i18n.changeLanguage(stored);
  }
}

/** date-fns locale matching the active interface language. */
export function getDateFnsLocale(): Locale {
  return i18n.language === "ru" ? dateFnsRu : dateFnsEn;
}

export default i18n;
