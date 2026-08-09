import "@testing-library/jest-dom";
// Initialise the default i18next instance directly from the
// dictionaries so components render real (English) texts in tests.
// Deliberately NOT via "@/i18n": that module pulls the settings/api
// chain, which unit tests replace with vi.mock — preloading it here
// would hand them the real implementation instead of the mock.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "@/i18n/locales/en";
import ru from "@/i18n/locales/ru";

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    ru: { translation: ru },
  },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  initAsync: false,
});
