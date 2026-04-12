import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import type { Chat, ChatSettings, Message } from "@/types/chat";
import { DEFAULT_CHAT_SETTINGS } from "@/types/chat";
import type { ExtendedUsage } from "@/types/stats";
import {
  createConversation,
  deleteConversation,
  fetchConversationMessages,
  fetchConversations,
  type StoredConversation,
  type StoredConversationMessage,
  updateConversationTitle,
} from "@/lib/bimemo-api";

export const DEFAULT_CHAT_TITLE = "New Chat";
const DEFAULT_MESSAGES_LIMIT = 200;
const IDLE_PROCESSING_STATUS = "idle";

type ConversationUpdate = {
  title?: string;
  bridgeProvider?: string | null;
  settings?: ChatSettings;
  processingStatus?: "idle" | "active";
  processingStage?: string;
  processingStartedAt?: Date | null;
  updatedAt?: Date;
};

type ConversationInsert = {
  id: string;
  title: string;
  bridgeProvider?: string | null;
  settings?: ChatSettings;
  processingStatus: "idle" | "active";
  processingStage: string;
  processingStartedAt?: Date;
  createdAt: Date;
  updatedAt: Date;
};

type StoredMessageMeta = {
  stats?: ExtendedUsage;
};

function parseMessageMeta(rawMeta: string | null): StoredMessageMeta | null {
  if (!rawMeta) {
    return null;
  }
  try {
    return JSON.parse(rawMeta) as StoredMessageMeta;
  } catch {
    return null;
  }
}

function mapSettings(
  raw: { show_sql?: boolean; show_thinking?: boolean } | undefined,
): ChatSettings {
  if (!raw) return { ...DEFAULT_CHAT_SETTINGS };
  return {
    showSql: raw.show_sql ?? DEFAULT_CHAT_SETTINGS.showSql,
    showThinking: raw.show_thinking ?? DEFAULT_CHAT_SETTINGS.showThinking,
  };
}

function mapConversation(
  conversation: StoredConversation,
  existingChat?: Chat,
): Chat {
  return {
    id: conversation.id,
    title: conversation.title || DEFAULT_CHAT_TITLE,
    bridgeProvider: conversation.bridge_provider ?? null,
    settings: mapSettings(conversation.settings),
    processingStatus:
      conversation.processing_status === "active" ? "active" : "idle",
    processingStage: conversation.processing_stage || "",
    processingStartedAt: conversation.processing_started_at
      ? new Date(conversation.processing_started_at)
      : undefined,
    messages: existingChat?.messages ?? [],
    createdAt: new Date(conversation.created_at),
    updatedAt: new Date(conversation.updated_at),
  };
}

export function extractMessageText(
  content: string | Array<{ type: string; text?: string }>,
): string {
  if (typeof content === "string") {
    return content;
  }
  const textParts = content
    .filter((part) => part.type === "text")
    .map((part) => part.text ?? "");
  return textParts.join(" ").trim();
}

export function extractMessageImages(
  content: string | Array<{ type: string; image_url?: { url?: string } }>,
): string[] | undefined {
  if (typeof content === "string") {
    return undefined;
  }
  const images = content
    .filter((part) => part.type === "image_url")
    .map((part) => part.image_url?.url ?? "")
    .filter((url) => Boolean(url));
  return images.length > 0 ? images : undefined;
}

function mapMessage(message: StoredConversationMessage): Message {
  const meta = parseMessageMeta(message.meta);
  const structuredContent = message.structured_content;
  const messageContent =
    structuredContent?.answer ?? extractMessageText(message.content);
  return {
    id: message.id,
    role: message.role,
    content: messageContent,
    images: extractMessageImages(message.content),
    thinking: structuredContent?.thinking ?? undefined,
    sql: structuredContent?.sql ?? undefined,
    suggestions: structuredContent?.suggestions ?? undefined,
    stats: meta?.stats,
    timestamp: new Date(message.created_at),
  };
}

function transientMessageMatchKey(message: Message): string {
  return `${message.role}:${message.content.trim()}`;
}

function mergeTransientMessageFields(
  storedMessages: Message[],
  existingMessages: Message[],
): Message[] {
  const existingMessagesById = new Map(
    existingMessages.map((message) => [message.id, message]),
  );
  const existingAssistantThinkingByKey = new Map<string, string>();

  for (const message of existingMessages) {
    if (message.role !== "assistant" || !message.thinking) {
      continue;
    }
    existingAssistantThinkingByKey.set(
      transientMessageMatchKey(message),
      message.thinking,
    );
  }

  return storedMessages.map((message) => {
    const existingById = existingMessagesById.get(message.id);
    if (existingById?.thinking) {
      return { ...message, thinking: existingById.thinking };
    }
    if (message.role !== "assistant") {
      return message;
    }
    const thinking = existingAssistantThinkingByKey.get(
      transientMessageMatchKey(message),
    );
    if (!thinking) {
      return message;
    }
    return { ...message, thinking };
  });
}

/**
 * Hook for managing chat state with server-backed persistence.
 */
export function useChat() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const activeChatIdRef = useRef<string | null>(activeChatId);
  const loadedConversationIdsRef = useRef<Set<string>>(new Set());
  const pendingConversationLoadsRef = useRef<Map<string, Promise<Message[]>>>(
    new Map(),
  );

  useEffect(() => {
    activeChatIdRef.current = activeChatId;
  }, [activeChatId]);

  const activeChat = useMemo(
    () => chats.find((chat) => chat.id === activeChatId) ?? null,
    [activeChatId, chats],
  );

  const refreshConversations = useCallback(async () => {
    const storedConversations = await fetchConversations();
    setChats((previousChats) => {
      const previousChatsById = new Map(
        previousChats.map((chat) => [chat.id, chat]),
      );
      return storedConversations.map((conversation) =>
        mapConversation(conversation, previousChatsById.get(conversation.id)),
      );
    });
    setActiveChatId((currentActiveChatId) => {
      if (
        currentActiveChatId &&
        storedConversations.some(
          (conversation) => conversation.id === currentActiveChatId,
        )
      ) {
        return currentActiveChatId;
      }
      return storedConversations[0]?.id ?? null;
    });
  }, []);

  const refreshConversationMessages = useCallback(
    async (conversationId: string): Promise<Message[]> => {
      const pendingLoad =
        pendingConversationLoadsRef.current.get(conversationId);
      if (pendingLoad) {
        return pendingLoad;
      }

      const loadPromise = (async (): Promise<Message[]> => {
        const storedMessages = await fetchConversationMessages(
          conversationId,
          DEFAULT_MESSAGES_LIMIT,
        );
        let messages = storedMessages.map(mapMessage);
        setChats((previousChats) =>
          previousChats.map((chat) => {
            if (chat.id !== conversationId) {
              return chat;
            }
            messages = mergeTransientMessageFields(messages, chat.messages);
            return { ...chat, messages };
          }),
        );
        loadedConversationIdsRef.current.add(conversationId);
        return messages;
      })();

      pendingConversationLoadsRef.current.set(conversationId, loadPromise);
      try {
        return await loadPromise;
      } finally {
        pendingConversationLoadsRef.current.delete(conversationId);
      }
    },
    [],
  );

  useEffect(() => {
    void (async () => {
      await refreshConversations();
      setIsReady(true);
    })();
  }, [refreshConversations]);

  useEffect(() => {
    if (!isReady || !activeChatId) {
      return;
    }
    if (!loadedConversationIdsRef.current.has(activeChatId)) {
      void refreshConversationMessages(activeChatId);
    }
  }, [activeChatId, isReady, refreshConversationMessages]);

  const createNewChat = useCallback(async (): Promise<Chat> => {
    const result = await createConversation("");
    const now = new Date();
    const newChat: Chat = {
      id: result.conversation_id,
      title: DEFAULT_CHAT_TITLE,
      bridgeProvider: null,
      settings: { ...DEFAULT_CHAT_SETTINGS },
      processingStatus: IDLE_PROCESSING_STATUS,
      processingStage: "",
      processingStartedAt: undefined,
      messages: [],
      createdAt: now,
      updatedAt: now,
    };
    loadedConversationIdsRef.current.add(newChat.id);
    setChats((previousChats) => [newChat, ...previousChats]);
    setActiveChatId(newChat.id);
    activeChatIdRef.current = newChat.id;
    return newChat;
  }, []);

  const addOrUpdateConversation = useCallback(
    (conversation: ConversationInsert) => {
      setChats((previousChats) => {
        const existingIndex = previousChats.findIndex(
          (chat) => chat.id === conversation.id,
        );
        if (existingIndex === -1) {
          return [
            {
              ...conversation,
              settings: conversation.settings ?? { ...DEFAULT_CHAT_SETTINGS },
              messages: [],
            },
            ...previousChats,
          ];
        }
        return previousChats.map((chat) =>
          chat.id === conversation.id
            ? {
                ...chat,
              title: conversation.title,
              bridgeProvider: conversation.bridgeProvider ?? chat.bridgeProvider,
              processingStatus: conversation.processingStatus,
                processingStage: conversation.processingStage,
                processingStartedAt: conversation.processingStartedAt,
                createdAt: conversation.createdAt,
                updatedAt: conversation.updatedAt,
              }
            : chat,
        );
      });
    },
    [],
  );

  const switchChat = useCallback(
    async (chatId: string) => {
      setActiveChatId(chatId);
      activeChatIdRef.current = chatId;
      if (!loadedConversationIdsRef.current.has(chatId)) {
        await refreshConversationMessages(chatId);
      }
    },
    [refreshConversationMessages],
  );

  const addMessage = useCallback((message: Message) => {
    const targetChatId = activeChatIdRef.current;
    if (!targetChatId) return;
    setChats((previousChats) =>
      previousChats.map((chat) =>
        chat.id === targetChatId
          ? {
              ...chat,
              messages: [...chat.messages, message],
              updatedAt: new Date(),
            }
          : chat,
      ),
    );
  }, []);

  const addMessageToConversation = useCallback(
    (conversationId: string, message: Message) => {
      setChats((previousChats) =>
        previousChats.map((chat) => {
          if (chat.id !== conversationId) {
            return chat;
          }
          if (chat.messages.some((existingMessage) => existingMessage.id === message.id)) {
            return chat;
          }
          return {
            ...chat,
            messages: [...chat.messages, message],
            updatedAt: message.timestamp,
          };
        }),
      );
    },
    [],
  );

  // Update an existing message (for streaming or final update)
  const updateMessage = useCallback(
    (messageId: string, updates: Partial<Message>) => {
      const targetChatId = activeChatIdRef.current;
      setChats((prev) =>
        prev.map((chat) => {
          if (chat.id !== targetChatId) return chat;

          return {
            ...chat,
            messages: chat.messages.map((msg) =>
              msg.id === messageId ? { ...msg, ...updates } : msg,
            ),
          };
        }),
      );
    },
    [],
  );

  // Delete a chat
  const deleteChat = useCallback(async (chatId: string) => {
    await deleteConversation(chatId);
    loadedConversationIdsRef.current.delete(chatId);
    pendingConversationLoadsRef.current.delete(chatId);
    setChats((prev) => prev.filter((chat) => chat.id !== chatId));
    if (activeChatIdRef.current === chatId) {
      setActiveChatId(null);
      activeChatIdRef.current = null;
    }
  }, []);

  const removeConversation = useCallback((chatId: string) => {
    loadedConversationIdsRef.current.delete(chatId);
    pendingConversationLoadsRef.current.delete(chatId);
    setChats((prev) => prev.filter((chat) => chat.id !== chatId));
    if (activeChatIdRef.current === chatId) {
      setActiveChatId(null);
      activeChatIdRef.current = null;
    }
  }, []);

  // Rename a chat
  const renameChat = useCallback(async (chatId: string, newTitle: string) => {
    const updatedChat = await updateConversationTitle(chatId, newTitle);
    setChats((prev) =>
      prev.map((chat) =>
        chat.id === chatId
          ? {
              ...chat,
              title: updatedChat.title || DEFAULT_CHAT_TITLE,
              updatedAt: new Date(updatedChat.updated_at),
            }
          : chat,
      ),
    );
  }, []);

  const updateConversation = useCallback(
    (conversationId: string, updates: ConversationUpdate) => {
      setChats((previousChats) =>
        previousChats.map((chat) =>
          chat.id === conversationId
            ? {
                ...chat,
                title: updates.title ?? chat.title,
                bridgeProvider:
                  updates.bridgeProvider === undefined
                    ? chat.bridgeProvider
                    : updates.bridgeProvider,
                settings: updates.settings ?? chat.settings,
                processingStatus:
                  updates.processingStatus ?? chat.processingStatus,
                processingStage: updates.processingStage ?? chat.processingStage,
                processingStartedAt:
                  updates.processingStartedAt === undefined
                    ? chat.processingStartedAt
                    : updates.processingStartedAt || undefined,
                updatedAt: updates.updatedAt ?? chat.updatedAt,
              }
            : chat,
        ),
      );
    },
    [],
  );

  const updateConversationActivity = useCallback(
    (conversationId: string, updatedAt: Date) => {
      updateConversation(conversationId, { updatedAt });
    },
    [updateConversation],
  );

  // Remove all messages after the given message ID (inclusive of the message itself is kept)
  const truncateAfterMessage = useCallback((messageId: string) => {
    const targetChatId = activeChatIdRef.current;
    setChats((prev) =>
      prev.map((chat) => {
        if (chat.id !== targetChatId) return chat;
        const index = chat.messages.findIndex((msg) => msg.id === messageId);
        if (index === -1) return chat;
        return {
          ...chat,
          messages: chat.messages.slice(0, index + 1),
          updatedAt: new Date(),
        };
      }),
    );
  }, []);

  const ensureActiveChat = useCallback(async (): Promise<Chat | null> => {
    const currentChatId = activeChatIdRef.current;
    if (currentChatId) {
      const currentChat =
        chats.find((chat) => chat.id === currentChatId) ?? null;
      if (currentChat) {
        if (!loadedConversationIdsRef.current.has(currentChatId)) {
          await refreshConversationMessages(currentChatId);
        }
        return currentChat;
      }
    }
    if (chats.length > 0) {
      const firstChat = chats[0];
      await switchChat(firstChat.id);
      return firstChat;
    }
    return createNewChat();
  }, [chats, createNewChat, refreshConversationMessages, switchChat]);

  return {
    chats,
    activeChat,
    activeChatId,
    isLoading,
    isReady,
    setIsLoading,
    createNewChat,
    addOrUpdateConversation,
    switchChat,
    addMessage,
    addMessageToConversation,
    updateMessage,
    deleteChat,
    removeConversation,
    renameChat,
    updateConversation,
    updateConversationActivity,
    truncateAfterMessage,
    ensureActiveChat,
    refreshConversations,
    refreshConversationMessages,
  };
}
