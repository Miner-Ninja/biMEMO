/**
 * S7.1 — the legacy dead error helpers are gone: the API module's
 * public surface no longer exposes them.
 */
import { describe, expect, it } from "vitest";
import * as bimemoApi from "@/services/bimemo-api";

describe("dead error helpers (S7.1)", () => {
  it.each([
    "ErrorInfo",
    "ERROR_SUGGESTIONS",
    "getErrorSuggestion",
    "UNKNOWN_ERROR_SUGGESTION",
  ])("%s is no longer exported from the API module", (name) => {
    expect(Object.keys(bimemoApi)).not.toContain(name);
  });
});
