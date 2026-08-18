import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchConversationMessages,
  type ConversationMessagePage,
  type StoredConversationMessage,
} from "@/lib/bimemo-api";

// R11 wire contract: GET /chat/conversations/{id}/messages is a cursor
// paginated page {messages, next_cursor, has_more}. The cursor is an
// opaque token: the client echoes it verbatim without parsing. The item
// model is unchanged.

function storedMessage(
  overrides: Partial<StoredConversationMessage> = {}
): StoredConversationMessage {
  return {
    id: "message-1",
    role: "user",
    content: "hello",
    conversation_id: "conversation-1",
    created_at: "2026-08-18T10:00:00Z",
    meta: null,
    ...overrides,
  };
}

function pageResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function capturedParams(fetchMock: { mock: { calls: unknown[][] } }): URLSearchParams {
  const input = fetchMock.mock.calls[0][0] as string;
  const query = input.split("?")[1] ?? "";
  return new URLSearchParams(query);
}

describe("fetchConversationMessages", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests the newest page with the default limit and no cursor", async () => {
    const body: ConversationMessagePage = {
      messages: [storedMessage()],
      next_cursor: "opaque-cursor-1",
      has_more: true,
    };
    const fetchMock = vi.fn(async () => pageResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchConversationMessages("conversation-1");

    const input = fetchMock.mock.calls[0][0] as string;
    expect(input).toMatch(
      /^\/chat\/conversations\/conversation-1\/messages\?limit=200$/
    );
    expect(capturedParams(fetchMock).has("cursor")).toBe(false);
    expect(page).toEqual(body);
  });

  it("echoes the opaque cursor verbatim on the older page", async () => {
    const opaque = "c1:2026-08-18T09:59:00:42";
    const body: ConversationMessagePage = {
      messages: [
        storedMessage({ id: "message-0", content: "older" }),
        storedMessage({ id: "message-1", content: "hello" }),
      ],
      next_cursor: "c2:2026-08-18T09:58:00:41",
      has_more: true,
    };
    const fetchMock = vi.fn(async () => pageResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchConversationMessages("conversation-1", 50, opaque);

    const params = capturedParams(fetchMock);
    expect(params.get("limit")).toBe("50");
    // The cursor is a black box: it goes out on the wire untouched.
    expect(params.get("cursor")).toBe(opaque);
    expect(page).toEqual(body);
    expect(page.has_more).toBe(true);
    expect(page.next_cursor).toBe("c2:2026-08-18T09:58:00:41");
  });

  it("keeps the stored message item shape unchanged", async () => {
    const item: StoredConversationMessage = {
      id: "message-9",
      role: "assistant",
      content: "answer",
      conversation_id: "conversation-1",
      created_at: "2026-08-18T10:00:01Z",
      meta: '{"stats":{"prompt_tokens":10,"completion_tokens":5}}',
      structured_content: {
        answer: "answer",
        thinking: "reasoning",
        sql: "SELECT 1",
        suggestions: ["Next step"],
      },
      pending_confirmations: [
        {
          action_id: "action-1",
          tool_name: "web_search",
          parameters: { query: "biMEMO" },
          grant_capable: true,
          url_host: null,
        },
      ],
      used_tools: [
        {
          name: "web_search",
          permission_mode: "once",
          authorization_source: "user",
          success: true,
        },
      ],
    };
    const body: ConversationMessagePage = {
      messages: [item],
      next_cursor: null,
      has_more: false,
    };
    const fetchMock = vi.fn(async () => pageResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchConversationMessages("conversation-1");

    expect(page.messages).toHaveLength(1);
    const received = page.messages[0];
    // Every item field round-trips unchanged.
    expect(received).toEqual(item);
    expect(received.id).toBe("message-9");
    expect(received.role).toBe("assistant");
    expect(received.content).toBe("answer");
    expect(received.created_at).toBe("2026-08-18T10:00:01Z");
    expect(received.meta).toBe(item.meta);
    expect(received.structured_content?.thinking).toBe("reasoning");
    expect(received.pending_confirmations?.[0].action_id).toBe("action-1");
    expect(received.used_tools?.[0].name).toBe("web_search");
    expect(page.has_more).toBe(false);
    expect(page.next_cursor).toBeNull();
  });
});
