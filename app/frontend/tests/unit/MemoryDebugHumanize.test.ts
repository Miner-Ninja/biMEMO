import { describe, it, expect } from "vitest";
import {
  humanizeRouteIntent,
  humanizeMismatchLabel,
  humanizeRecorderDecision,
} from "@/features/memory/components/humanize";
import en from "@/i18n/locales/en";

import type { TFunction } from "i18next";

const t = ((key: string) => key) as unknown as TFunction;

describe("humanizeRouteIntent", () => {
  it("known code returns translation key (S4.1)", () => {
    expect(humanizeRouteIntent(t, "non_memory_question")).toEqual({
      text: "memoryDebug.phraseNonMemoryQuestion",
    });
    expect(humanizeRouteIntent(t, "memory_question")).toEqual({
      text: "memoryDebug.phraseMemoryQuestion",
    });
  });

  it("unknown code returns raw object (S5.1)", () => {
    expect(humanizeRouteIntent(t, "weird_code")).toEqual({
      text: "weird_code",
      raw: true,
    });
  });
});

describe("humanizeMismatchLabel", () => {
  it("known codes return translation keys", () => {
    expect(humanizeMismatchLabel(t, "no_mismatch")).toEqual({
      text: "memoryDebug.phraseNoMismatch",
    });
    expect(humanizeMismatchLabel(t, "intent_changed")).toEqual({
      text: "memoryDebug.phraseIntentChanged",
    });
  });
});

describe("humanizeRecorderDecision", () => {
  it("known codes return translation keys", () => {
    expect(humanizeRecorderDecision(t, "stored")).toEqual({
      text: "memoryDebug.phraseRecStored",
    });
    expect(humanizeRecorderDecision(t, "unsafe_output")).toEqual({
      text: "memoryDebug.phraseRecUnsafe",
    });
  });
});

describe("i18n key completeness (R7)", () => {
  it("all humanize keys exist in en.ts", () => {
    const enKeys = Object.keys(en.memoryDebug);
    const humanizeKeys = [
      "phraseNonMemoryQuestion",
      "phraseMemoryQuestion",
      "phraseEvidenceQuestion",
      "phraseNoMismatch",
      "phraseIntentChanged",
      "phraseRoleFilterChanged",
      "phraseLongTermFlagChanged",
      "phraseSourceChanged",
      "phraseRecStored",
      "phraseRecDuplicate",
      "phraseRecNotWorthy",
      "phraseRecUnsafe",
      "phraseRecParseFailed",
      "phraseRecLlmError",
      "phraseRecRateLimited",
    ];
    humanizeKeys.forEach((key) => {
      expect(enKeys).toContain(key);
    });
  });
});
