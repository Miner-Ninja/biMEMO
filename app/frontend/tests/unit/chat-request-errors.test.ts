import { afterEach, describe, expect, it, vi } from "vitest";
import {
  isBrowserNetworkFetchError,
  shouldKeepStreamedAssistantResponseAfterRequestError,
} from "@/lib/chat-request-errors";
import { apiRequest } from "@/lib/bimemo-api";

describe("isBrowserNetworkFetchError", () => {
  it("matches Firefox fetch failures", () => {
    expect(
      isBrowserNetworkFetchError(
        new TypeError("NetworkError when attempting to fetch resource.")
      )
    ).toBe(true);
  });

  it("matches Chromium fetch failures", () => {
    expect(isBrowserNetworkFetchError(new TypeError("Failed to fetch"))).toBe(true);
  });

  it("does not match normal backend errors", () => {
    expect(isBrowserNetworkFetchError(new Error("Internal Server Error"))).toBe(false);
  });
});

describe("shouldKeepStreamedAssistantResponseAfterRequestError", () => {
  it("keeps a streamed answer after a network fetch failure", () => {
    expect(
      shouldKeepStreamedAssistantResponseAfterRequestError(
        new TypeError("NetworkError when attempting to fetch resource."),
        "Visible answer",
        ""
      )
    ).toBe(true);
  });

  it("keeps streamed thinking after a network fetch failure", () => {
    expect(
      shouldKeepStreamedAssistantResponseAfterRequestError(
        new TypeError("Failed to fetch"),
        "",
        "Thinking"
      )
    ).toBe(true);
  });

  it("does not hide network failures before streaming starts", () => {
    expect(
      shouldKeepStreamedAssistantResponseAfterRequestError(
        new TypeError("Failed to fetch"),
        "",
        ""
      )
    ).toBe(false);
  });

  it("does not hide backend failures after streaming starts", () => {
    expect(
      shouldKeepStreamedAssistantResponseAfterRequestError(
        new Error("Internal Server Error"),
        "Visible answer",
        ""
      )
    ).toBe(false);
  });
});

describe("apiRequest 204 handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves to null on 204 without parsing the empty body", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.url;
      if (url === "/csrf-token") {
        return new Response(JSON.stringify({ token: "csrf-token" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest("/files/file-1", { method: "DELETE" });

    expect(result).toBeNull();
  });
});
