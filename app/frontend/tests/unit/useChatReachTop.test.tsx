import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChat } from "@/hooks/useChat";
import type { ConversationMessagePage } from "@/lib/bimemo-api";

const {
  fetchConversations,
  fetchConversationMessages,
  createConversation,
  deleteConversation,
  updateConversationTitle,
} = vi.hoisted(() => ({
  fetchConversations: vi.fn(),
  fetchConversationMessages: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  updateConversationTitle: vi.fn(),
}));

vi.mock("@/lib/bimemo-api", () => ({
  createConversation,
  deleteConversation,
  fetchConversationMessages,
  fetchConversations,
  updateConversationTitle,
}));

function conversation(id: string, title: string) {
  return {
    id,
    title,
    processing_status: "idle",
    processing_stage: "",
    processing_started_at: null,
    created_at: "2026-03-28T10:00:00Z",
    updated_at: "2026-03-28T10:00:00Z",
  };
}

function message(id: string, conversationId: string, content: string) {
  return {
    id,
    role: "user" as const,
    content,
    conversation_id: conversationId,
    created_at: "2026-03-28T10:00:00Z",
    meta: null,
  };
}

function page(
  messages: ReturnType<typeof message>[],
  nextCursor: string | null,
  hasMore: boolean
): ConversationMessagePage {
  return { messages, next_cursor: nextCursor, has_more: hasMore };
}

// R11.3: the reach-top load is guarded — one older-page load per
// conversation at a time, and none at all once the history is exhausted.
describe("useChat reach-top (loadOlderMessages)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchConversations.mockResolvedValue([conversation("chat-1", "Chat 1")]);
    fetchConversationMessages.mockImplementation(async () => {
      await Promise.resolve();
      return page([], null, false);
    });
    createConversation.mockResolvedValue({ conversation_id: "chat-3" });
    deleteConversation.mockResolvedValue({ ok: true });
    updateConversationTitle.mockImplementation(async (id: string, title: string) =>
      conversation(id, title)
    );
  });

  it("ignores repeated reach-top events while a load is in flight", async () => {
    // The newest page says more history exists; the older page request
    // hangs until the test releases it.
    let releaseOlderPage: (value: ConversationMessagePage) => void;
    const olderPage = new Promise<ConversationMessagePage>(
      (resolve) => (releaseOlderPage = resolve)
    );
    fetchConversationMessages.mockImplementation(
      async (conversationId: string, _limit?: number, cursor?: string | null) => {
        if (cursor === undefined || cursor === null) {
          return page([message("msg-new", conversationId, "newest")], "cursor-1", true);
        }
        return olderPage;
      }
    );

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.isReady).toBe(true));
    await act(async () => {
      await result.current.switchChat("chat-1");
    });
    expect(fetchConversationMessages).toHaveBeenCalledTimes(1);

    // Three reach-top events in a row (scroll jitter at the top).
    const first = result.current.loadOlderMessages("chat-1");
    const second = result.current.loadOlderMessages("chat-1");
    const third = result.current.loadOlderMessages("chat-1");
    // Exactly one request went out for the older page.
    expect(fetchConversationMessages).toHaveBeenCalledTimes(2);

    releaseOlderPage!(page([message("msg-old", "chat-1", "older")], null, false));
    await act(async () => {
      await Promise.all([first, second, third]);
    });
    // The in-flight guard held: no request was added by the repeats.
    expect(fetchConversationMessages).toHaveBeenCalledTimes(2);
  });

  it("stops requesting once the server reports the history is exhausted", async () => {
    fetchConversationMessages.mockImplementation(
      async (conversationId: string, _limit?: number, cursor?: string | null) => {
        if (cursor === undefined || cursor === null) {
          return page([message("msg-new", conversationId, "newest")], "cursor-1", true);
        }
        return page([message("msg-old", conversationId, "oldest")], null, false);
      }
    );

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.isReady).toBe(true));
    await act(async () => {
      await result.current.switchChat("chat-1");
    });

    await act(async () => {
      await result.current.loadOlderMessages("chat-1");
    });
    expect(fetchConversationMessages).toHaveBeenCalledTimes(2);

    // has_more=false: further reach-top events are no-ops.
    await act(async () => {
      await result.current.loadOlderMessages("chat-1");
    });
    await act(async () => {
      await result.current.loadOlderMessages("chat-1");
    });
    expect(fetchConversationMessages).toHaveBeenCalledTimes(2);
  });

  it("does nothing for a conversation that has no loaded page state yet", async () => {
    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.isReady).toBe(true));

    // No switch/load happened: there is no cursor to continue from.
    await act(async () => {
      await result.current.loadOlderMessages("chat-1");
    });
    expect(fetchConversationMessages).not.toHaveBeenCalled();
  });
});
