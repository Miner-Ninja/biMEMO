import { describe, it, expect } from "vitest";
import { shouldUseStructuredPseudoStream } from "@/features/chat/model/pseudo-stream";

describe("shouldUseStructuredPseudoStream", () => {
  const structured = { answer: "Hello", thinking: null, sql: null };

  it("returns false when stream_enabled flag is true", () => {
    expect(shouldUseStructuredPseudoStream(structured, "", "", true)).toBe(false);
  });

  it("returns false when stream_enabled is true even with empty structured content", () => {
    expect(
      shouldUseStructuredPseudoStream(
        { answer: "", thinking: null, sql: null },
        "",
        "",
        true
      )
    ).toBe(false);
  });

  it("returns true with structured answer and no streaming when flag is false", () => {
    expect(shouldUseStructuredPseudoStream(structured, "", "", false)).toBe(true);
  });

  it("returns true when flag is undefined (older orchestrator)", () => {
    expect(shouldUseStructuredPseudoStream(structured, "", "", undefined)).toBe(true);
  });

  it("returns false when streamedContent is non-empty", () => {
    expect(shouldUseStructuredPseudoStream(structured, "Hel", "", false)).toBe(false);
  });

  it("returns false when streamedThinking is non-empty", () => {
    expect(shouldUseStructuredPseudoStream(structured, "", "plan", false)).toBe(false);
  });

  it("returns false when structuredContent is null", () => {
    expect(shouldUseStructuredPseudoStream(null, "", "", false)).toBe(false);
  });

  it("returns false when structuredContent has no visible material", () => {
    expect(
      shouldUseStructuredPseudoStream(
        { answer: "", thinking: null, sql: null },
        "",
        "",
        false
      )
    ).toBe(false);
  });
});
