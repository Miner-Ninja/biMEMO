/**
 * formatContent must always return a string: an entry with no content
 * (undefined) crashed the whole Memory Debug page via text.slice.
 */

import { describe, expect, it } from "vitest";
import { formatContent } from "@/features/memory/components/shared";

describe("formatContent", () => {
  it("undefined content yields an empty string, not undefined", () => {
    const result = formatContent(undefined);
    expect(typeof result.text).toBe("string");
    expect(result.text).toBe("");
  });

  it("plain string passes through", () => {
    expect(formatContent("hello").text).toBe("hello");
  });

  it("objects are serialized", () => {
    expect(formatContent({ a: 1 }).text).toBe('{"a":1}');
  });
});
