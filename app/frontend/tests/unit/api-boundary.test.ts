/**
 * Unit tests for the apiRequest response-boundary contract (W-B5).
 *
 * The optional zod schema is the last step of apiRequest: it parses the
 * final JSON body after the 401/403 retry logic and the 204 handling.
 * A malformed body throws ContractError (an ApiError with status 0), so
 * every existing `instanceof ApiError` consumer handles it unchanged.
 */

import { z } from "zod";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest, ApiError, ContractError } from "@/services/bimemo-api";

const itemSchema = z.looseObject({
  id: z.string(),
  title: z.string(),
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** One fetch stub that serves auth endpoints and a fixed target response. */
function stubFetch(target: Response) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/refresh") {
      return new Response(null, { status: 200 });
    }
    if (url === "/csrf-token") {
      return jsonResponse({ token: "csrf-test" });
    }
    return target;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("apiRequest boundary", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the raw body unchanged when no schema is given", async () => {
    stubFetch(jsonResponse({ id: "a", title: "t", extra: 42 }));

    const data = await apiRequest<{ id: string; title: string }>("/chat/conversations");

    expect(data).toEqual({ id: "a", title: "t", extra: 42 });
  });

  it("returns the parsed value for a conforming body with a schema", async () => {
    stubFetch(jsonResponse({ id: "a", title: "t" }));

    const data = await apiRequest("/chat/conversations", {}, itemSchema);

    expect(data).toEqual({ id: "a", title: "t" });
    expect(data).toHaveProperty("id", "a");
  });

  it("throws ContractError for a malformed body, naming the first mismatch", async () => {
    stubFetch(jsonResponse({ id: "a", title: 7 }));

    const promise = apiRequest("/chat/conversations", {}, itemSchema);

    await expect(promise).rejects.toThrow(ContractError);
    await expect(promise).rejects.toThrow(/title/);
    await expect(promise).rejects.toThrow(/expected string, received number/);
  });

  it("keeps ContractError an ApiError with status 0 for existing handlers", async () => {
    stubFetch(jsonResponse({ id: "a", title: 7 }));

    const error = await apiRequest("/chat/conversations", {}, itemSchema).catch(
      (thrown: unknown) => thrown
    );

    expect(error).toBeInstanceOf(ContractError);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).name).toBe("ContractError");
  });

  it("preserves unknown keys: a loose object passes extras through", async () => {
    stubFetch(jsonResponse({ id: "a", title: "t", unexpected: [1, 2] }));

    const data = await apiRequest("/chat/conversations", {}, itemSchema);

    expect(data).toHaveProperty("unexpected", [1, 2]);
  });

  it("still resolves 204 to null when a schema is provided", async () => {
    stubFetch(new Response(null, { status: 204 }));

    const data = await apiRequest("/files/1", { method: "DELETE" }, itemSchema);

    expect(data).toBeNull();
  });

  it("validates the retried response exactly once after a 401 refresh", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/refresh") {
        return new Response(null, { status: 200 });
      }
      calls += 1;
      if (calls === 1) {
        return new Response(null, { status: 401 });
      }
      return jsonResponse({ id: "a", title: 7 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/chat/conversations", {}, itemSchema)).rejects.toThrow(
      ContractError
    );
    // One 401, one refresh, one CSRF re-fetch, one retried (malformed)
    // response — the schema sees exactly the final body.
    expect(fetchMock.mock.calls.length).toBe(4);
  });

  it("throws ApiError (not ContractError) for non-2xx responses with a schema", async () => {
    stubFetch(jsonResponse({ detail: "not found" }, 404));

    const error = await apiRequest("/chat/conversations", {}, itemSchema).catch(
      (thrown: unknown) => thrown
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(ContractError);
    expect((error as ApiError).status).toBe(404);
  });
});
