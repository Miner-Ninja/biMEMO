import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChat } from "@/hooks/useChat";

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

describe("useChat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchConversations.mockResolvedValue([
      conversation("chat-1", "Chat 1"),
      conversation("chat-2", "Chat 2"),
    ]);
    fetchConversationMessages.mockImplementation(
      async (conversationId: string) => {
        await Promise.resolve();
        return [message(`msg-${conversationId}`, conversationId, conversationId)];
      },
    );
    createConversation.mockResolvedValue({ conversation_id: "chat-3" });
    deleteConversation.mockResolvedValue({ ok: true });
    updateConversationTitle.mockImplementation(async (id: string, title: string) =>
      conversation(id, title),
    );
  });

  it("loads one unopened chat only once when selection and effect happen together", async () => {
    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.isReady).toBe(true));
    await waitFor(() =>
      expect(fetchConversationMessages).toHaveBeenCalledWith("chat-1", 200),
    );

    fetchConversationMessages.mockClear();

    await act(async () => {
      await result.current.switchChat("chat-2");
    });

    await waitFor(() =>
      expect(fetchConversationMessages).toHaveBeenCalledWith("chat-2", 200),
    );
    expect(fetchConversationMessages).toHaveBeenCalledTimes(1);
  });

  it("keeps local thinking when the same assistant answer is reread from the server", async () => {
    fetchConversations.mockResolvedValue([conversation("chat-1", "Chat 1")]);
    fetchConversationMessages.mockResolvedValueOnce([
      message("stored-user-1", "chat-1", "hello"),
    ]);
    fetchConversationMessages.mockResolvedValueOnce([
      message("stored-user-1", "chat-1", "hello"),
      {
        id: "stored-assistant-1",
        role: "assistant" as const,
        content: "final answer",
        conversation_id: "chat-1",
        created_at: "2026-03-28T10:00:01Z",
        meta: null,
      },
    ]);

    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.isReady).toBe(true));
    await waitFor(() =>
      expect(fetchConversationMessages).toHaveBeenCalledWith("chat-1", 200),
    );

    await act(async () => {
      result.current.addMessage({
        id: "local-assistant-temp",
        role: "assistant",
        content: "final answer",
        thinking: "temporary visible reasoning",
        timestamp: new Date("2026-03-28T10:00:01Z"),
      });
    });

    await act(async () => {
      await result.current.refreshConversationMessages("chat-1");
    });

    await waitFor(() => {
      expect(result.current.activeChat?.messages).toHaveLength(2);
    });

    expect(result.current.activeChat?.messages[1].content).toBe("final answer");
    expect(result.current.activeChat?.messages[1].thinking).toBe(
      "temporary visible reasoning",
    );
  });

  it("reads saved thinking from structured assistant history", async () => {
    fetchConversations.mockResolvedValue([conversation("chat-1", "Chat 1")]);
    fetchConversationMessages.mockResolvedValueOnce([
      {
        id: "stored-assistant-1",
        role: "assistant" as const,
        content: "final answer",
        conversation_id: "chat-1",
        created_at: "2026-03-28T10:00:01Z",
        meta: '{"stats":{"prompt_tokens":10,"completion_tokens":5}}',
        structured_content: {
          answer: "final answer",
          thinking: "stored reasoning",
          sql: "SELECT 1",
          suggestions: ["Next step"],
        },
      },
    ]);

    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.isReady).toBe(true));
    await waitFor(() =>
      expect(fetchConversationMessages).toHaveBeenCalledWith("chat-1", 200),
    );

    expect(result.current.activeChat?.messages[0].content).toBe("final answer");
    expect(result.current.activeChat?.messages[0].thinking).toBe(
      "stored reasoning",
    );
    expect(result.current.activeChat?.messages[0].sql).toBe("SELECT 1");
    expect(result.current.activeChat?.messages[0].suggestions).toEqual([
      "Next step",
    ]);
    expect(result.current.activeChat?.messages[0].stats?.prompt_tokens).toBe(10);
    expect(result.current.activeChat?.messages[0].stats?.completion_tokens).toBe(
      5,
    );
  });

  it("adds an inbound bridge message to the chosen chat without reloading history", async () => {
    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.isReady).toBe(true));
    await waitFor(() =>
      expect(fetchConversationMessages).toHaveBeenCalledWith("chat-1", 200),
    );

    await act(async () => {
      result.current.addMessageToConversation("chat-2", {
        id: "bridge-msg-1",
        role: "user",
        content: "from discord",
        timestamp: new Date("2026-03-28T10:05:00Z"),
      });
    });

    const targetChat = result.current.chats.find((chat) => chat.id === "chat-2");
    expect(targetChat?.messages.at(-1)?.id).toBe("bridge-msg-1");
    expect(targetChat?.messages.at(-1)?.content).toBe("from discord");
  });

  it("removes one chat locally without sending a delete request", async () => {
    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.isReady).toBe(true));

    await act(async () => {
      result.current.removeConversation("chat-2");
    });

    expect(result.current.chats.map((chat) => chat.id)).toEqual(["chat-1"]);
    expect(deleteConversation).not.toHaveBeenCalled();
  });

  it("adds a new chat card locally without reloading the full list", async () => {
    const { result } = renderHook(() => useChat());

    await waitFor(() => expect(result.current.isReady).toBe(true));

    await act(async () => {
      result.current.addOrUpdateConversation({
        id: "chat-3",
        title: "Server chat",
        processingStatus: "idle",
        processingStage: "",
        createdAt: new Date("2026-03-28T10:10:00Z"),
        updatedAt: new Date("2026-03-28T10:10:00Z"),
      });
    });

    expect(result.current.chats.map((chat) => chat.id)).toContain("chat-3");
    expect(fetchConversations).toHaveBeenCalledTimes(1);
  });
});
