import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate } from "react-router-dom";
import i18n from "@/i18n";
import { Layout } from "@/components/layout/Layout";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { MessageList } from "@/components/chat/MessageList";
import { ChatBridgeDetailsModal } from "@/components/chat/ChatBridgeDetailsModal";
import { ChatInput, type ChatInputHandle } from "@/components/chat/ChatInput";
import { ToastContainer, useToasts } from "@/components/ui/Toast";
import { ReauthModal } from "@/components/ReauthModal";
import {
  buildAssistantMessageFields,
  DEFAULT_CHAT_TITLE,
  extractMessageImages,
  extractMessageText,
  useChat,
} from "@/hooks/useChat";
import { useProcessingStages } from "@/hooks/useProcessingStages";
import {
  useRealtimeStream,
  type ConnectionStatus,
  type RealtimeEvent,
} from "@/hooks/useRealtimeStream";
import { useAuth } from "@/hooks/useAuth";
import { DEFAULT_CHAT_SETTINGS } from "@/types/chat";
import { updateChatSettings } from "@/lib/bimemo-api";
import { useConfirm } from "@/hooks/useConfirm";
import { PendingConfirmationPanel } from "@/components/chat/PendingConfirmationPanel";
import {
  apiRequest,
  ApiError,
  confirmAction,
  denyAction,
  createChatBridgeLink,
  disableChatBridgeLink,
  enableChatBridgeLink,
  fetchChatBridgeForConversation,
  fetchChatBridgeProviders,
  updateChatBridgeLinkSettings,
  fetchFiles,
  checkTtsStatus,
  synthesizeSpeech,
  uploadFile,
} from "@/lib/bimemo-api";
import type {
  ChatBridgeLink,
  ChatBridgeProviderItem,
  IndexedFile,
} from "@/lib/bimemo-api";
import { generateId } from "@/lib/utils";
import { shouldUseStructuredPseudoStream } from "@/lib/pseudo-stream";
import { shouldKeepStreamedAssistantResponseAfterRequestError } from "@/lib/chat-request-errors";
import {
  chatInputIsDisabled,
  classifyPausedResumeEvent,
  bindPendingInputReply,
  markStreamTerminalError,
  resolveStreamErrorMessage,
  runtimeBootstrapHasTerminalEvent,
  runtimeBootstrapPausedEvent,
  shouldApplyHttpChatResponse,
  shouldFinalizeAssistantStream,
  type ActiveAssistantStreamState,
  type PausedRunBinding,
  type RuntimeBootstrapPayload,
} from "@/lib/chat-response-source";
import { COMMAND_HISTORY_LIMIT, getUiSetting, setUiSetting } from "@/lib/ui-settings";
import type {
  Chat,
  ConfirmationScope,
  Message,
  PendingConfirmation,
  PendingInput,
  ProcessingStage,
  UsedTool,
} from "@/types/chat";
import { TOOL_LABELS } from "@/types/chat";
import type { ExtendedUsage } from "@/types/stats";
import type { UploadProgressData } from "@/components/ui/UploadProgressPanel";

type ChatResponsePayload = {
  conversation_id?: string;
  request_id?: string;
  run_id?: string;
  stream_started?: boolean;
  retrieved_memories?: { summary: string; date: string }[];
  transcription?: string;
  stats?: ExtendedUsage;
  structured_content?: {
    answer: string;
    thinking?: string | null;
    suggestions?: string[];
  };
  pending_confirmations?: {
    action_id: string;
    tool_name: string;
    parameters: Record<string, unknown>;
    grant_capable?: boolean;
    url_host?: string | null;
  }[];
  pause_reason?: "confirmation" | "missing_input" | null;
  pending_input?: Omit<PendingInput, "run_id"> | null;
  used_tools?: UsedTool[];
  stream_enabled?: boolean;
  outcome_kind?: "final" | "paused" | "failed" | "cancelled";
  finalization_id?: string | null;
  error?: { code: string; message: string; retryable?: boolean } | null;
};

type TokenEventPayload = {
  type?: string;
  content?: string;
};

type ToolStatusEventPayload = {
  type?: string;
  name?: string;
  status?: string;
  duration_ms?: number;
};

type MultimodalContentPart =
  { type: "text"; text?: string } | { type: "image_url"; image_url?: { url?: string } };

type BridgeInboundMessageEventPayload = {
  conversation_id?: string;
  message?: {
    id?: string;
    role?: "user" | "assistant";
    content?: string | MultimodalContentPart[];
    created_at?: string;
    source?: string;
    author_name?: string;
  };
};

type BridgeLinkUpdatedEventPayload = {
  conversation_id?: string;
  bridge_link?: ChatBridgeLink;
};

type ConversationUpdatedEventPayload = {
  conversation_id?: string;
  title?: string;
  bridge_provider?: string | null;
  settings?: {
    show_thinking?: boolean;
    show_suggestions?: boolean;
    thinking_enabled?: boolean;
  };
  processing_status?: "idle" | "active";
  processing_stage?: string;
  processing_started_at?: string | null;
  updated_at?: string;
};

type ConversationDeletedEventPayload = {
  conversation_id?: string;
};

type ConversationCreatedEventPayload = {
  conversation_id?: string;
  title?: string;
  bridge_provider?: string | null;
  processing_status?: "idle" | "active";
  processing_stage?: string;
  processing_started_at?: string | null;
  updated_at?: string;
  created_at?: string;
};

type ChatErrorEventPayload = {
  conversation_id?: string;
  message?: string;
};

type PendingBridgeAction = {
  conversationId: string;
  provider: string;
  enabled: boolean;
};

type PendingBridgeSettingsSave = {
  bridgeLinkId: string;
  conversationId: string;
  deliveryMode: string;
  personaMode: string;
  personaUserId: string;
};

const DISCORD_BRIDGE_PROVIDER = "discord";
const PERSONA_DELIVERY_MODE = "persona_user_and_bot_assistant";
const NEUTRAL_PERSONA_MODE = "neutral";
const PSEUDO_STREAM_INTERVAL_MS = 40;
const DEFAULT_PSEUDO_STREAM_TOKENS_PER_SECOND = 30;
const AVERAGE_CHARACTERS_PER_TOKEN = 4;
const MIN_PSEUDO_STREAM_CHUNK_SIZE = 1;

type StructuredChatSections = {
  thinking: string;
  answer: string;
  suggestions?: string[];
};

type PseudoStreamSectionInput = {
  assistantMessageId: string;
  runId: number;
  stats: ExtendedUsage | undefined;
  text: string;
  buildUpdate: (visibleText: string) => Partial<Message>;
};

type StructuredAssistantPlaybackInput = {
  assistantMessageId: string;
  stats: ExtendedUsage | undefined;
  sections: StructuredChatSections;
  pendingConfirmations?: PendingConfirmation[];
  usedTools?: UsedTool[];
};

type ApplyAssistantResponseInput = {
  assistantMessageId: string;
  response: ChatResponsePayload;
};

type ChatFinalEventPayload = ChatResponsePayload & {
  type?: string;
  conversation_id?: string;
};

function buildServerProcessingStage(
  stageLabel: string,
  startedAt?: Date
): ProcessingStage[] {
  const wallClockDelayMs = startedAt
    ? Math.max(0, Date.now() - startedAt.getTime())
    : 0;
  return [
    {
      id: "server-processing-stage",
      label: stageLabel || i18n.t("chat.processing"),
      status: "active",
      startedAt: performance.now() - wallClockDelayMs,
    },
  ];
}

function bridgeCreateSettings(
  provider: string,
  defaultPersonaUserId: string
): {
  deliveryMode?: string;
  personaMode?: string;
  personaUserId?: string;
} {
  if (provider !== DISCORD_BRIDGE_PROVIDER) {
    return {};
  }
  return {
    deliveryMode: PERSONA_DELIVERY_MODE,
    personaMode: defaultPersonaUserId ? "mapped_user" : NEUTRAL_PERSONA_MODE,
    personaUserId: defaultPersonaUserId,
  };
}

function buildAssistantMessageUpdate(
  base: Partial<Message>,
  thinking: string
): Partial<Message> {
  // Never overwrite already received thinking with an empty value.
  if (!thinking) {
    return base;
  }

  return {
    ...base,
    thinking,
  };
}

function sleep(milliseconds: number): Promise<void> {
  // Wait for one short pseudo-stream interval.
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function pseudo_stream_tokens_per_second(stats: ExtendedUsage | undefined): number {
  // Resolve the playback speed for one finished assistant response.
  const generationMilliseconds = stats?.timing?.generation_ms ?? 0;
  const completionTokens = stats?.completion_tokens ?? 0;
  if (generationMilliseconds <= 0 || completionTokens <= 0) {
    return DEFAULT_PSEUDO_STREAM_TOKENS_PER_SECOND;
  }
  return completionTokens / (generationMilliseconds / 1000);
}

function pseudo_stream_chunk_size(stats: ExtendedUsage | undefined): number {
  // Calculate the visible chunk size for one pseudo-stream step.
  const charactersPerSecond =
    pseudo_stream_tokens_per_second(stats) * AVERAGE_CHARACTERS_PER_TOKEN;
  const chunkSize = Math.round(
    charactersPerSecond * (PSEUDO_STREAM_INTERVAL_MS / 1000)
  );
  return Math.max(MIN_PSEUDO_STREAM_CHUNK_SIZE, chunkSize);
}

function should_use_structured_pseudo_stream(
  structuredContent: ChatResponsePayload["structured_content"] | undefined,
  streamedContent: string,
  streamedThinking: string,
  streamEnabled: boolean | undefined
): structuredContent is NonNullable<ChatResponsePayload["structured_content"]> {
  return shouldUseStructuredPseudoStream(
    structuredContent ?? null,
    streamedContent,
    streamedThinking,
    streamEnabled
  );
}

function visible_assistant_answer(
  structuredContent: ChatResponsePayload["structured_content"] | undefined,
  streamedContent: string
): string {
  // Resolve the final visible assistant answer from one response payload.
  if (structuredContent) {
    return structuredContent.answer;
  }
  return streamedContent;
}

function nextConversationTitle(
  currentTitle: string | undefined,
  messageContent: string
): string {
  const normalizedCurrentTitle = (currentTitle || "").trim();
  if (normalizedCurrentTitle && normalizedCurrentTitle !== DEFAULT_CHAT_TITLE) {
    return normalizedCurrentTitle;
  }
  const collapsedContent = messageContent.split(/\s+/).join(" ").trim();
  if (!collapsedContent) {
    return currentTitle || DEFAULT_CHAT_TITLE;
  }
  return collapsedContent.slice(0, 80);
}

function splitAttachedFiles(files: File[]): {
  imageFiles: File[];
  documentFiles: File[];
} {
  const imageMimeTypes = new Set([
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
  ]);
  const imageFiles: File[] = [];
  const documentFiles: File[] = [];
  for (const file of files) {
    if (imageMimeTypes.has(file.type)) {
      imageFiles.push(file);
      continue;
    }
    documentFiles.push(file);
  }
  return { imageFiles, documentFiles };
}

function bridgeProviderMap(chats: Chat[]): Record<string, string> {
  return chats.reduce<Record<string, string>>((accumulator, chat) => {
    if (chat.bridgeProvider) {
      accumulator[chat.id] = chat.bridgeProvider;
    }
    return accumulator;
  }, {});
}

function bridgeProviderFromLink(bridgeLink: ChatBridgeLink | null): string | null {
  if (!bridgeLink || bridgeLink.status !== "active") {
    return null;
  }
  return bridgeLink.provider;
}

async function imageFileDataUrl(file: File): Promise<string> {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function ChatPage() {
  const { t } = useTranslation();
  const {
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
    updateMessageInConversation,
    deleteChat,
    removeConversation,
    renameChat,
    truncateAfterMessage,
    updateConversation,
    updateConversationActivity,
    ensureActiveChat,
    refreshConversations,
    refreshConversationMessages,
  } = useChat();

  const { logout } = useAuth();

  const { conversationId: urlConversationId } = useParams();
  const navigate = useNavigate();

  // The URL is the source of truth for the active conversation (Option A).
  //   /c/:id  -> select that chat (so a refresh restores where you were);
  //   bare /  -> canonicalize to the first chat's id in the URL;
  //   unknown id (deleted chat / stale bookmark) -> fall back to /.
  useEffect(() => {
    if (!isReady) {
      return;
    }
    if (urlConversationId) {
      if (urlConversationId === activeChatId) {
        return;
      }
      if (chats.some((chat) => chat.id === urlConversationId)) {
        void switchChat(urlConversationId);
      } else if (chats.length > 0) {
        navigate("/", { replace: true });
      }
      return;
    }
    if (chats.length > 0) {
      navigate(`/c/${chats[0].id}`, { replace: true });
    }
  }, [urlConversationId, isReady, chats, activeChatId, switchChat, navigate]);

  const chatSettings = activeChat?.settings ?? DEFAULT_CHAT_SETTINGS;
  // One switch: thinkingEnabled gates both generation and display.
  const showThinking = chatSettings.thinkingEnabled;
  const showSuggestions = chatSettings.showSuggestions;
  const chatInputRef = useRef<ChatInputHandle>(null);
  const assistantMessageIdRef = useRef<string | null>(null);
  const streamedContentRef = useRef("");
  const streamedThinkingRef = useRef("");
  // Confirmations that arrived as realtime `confirmation` events for the
  // current assistant turn; accumulated so multiple RED tools each get a card.
  const pendingConfirmationsRef = useRef<PendingConfirmation[]>([]);
  const pendingInputRef = useRef<PendingInput | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const processedFinalizationIdsRef = useRef<Set<string>>(new Set());
  const pseudoStreamRunIdRef = useRef(0);
  const stoppedAssistantMessageIdsRef = useRef<Set<string>>(new Set());
  const pausedRunRef = useRef<PausedRunBinding | null>(null);
  const activeAssistantStreamRef = useRef<ActiveAssistantStreamState>({
    assistantMessageId: null,
    conversationId: null,
    requestId: null,
    runId: null,
    voiceUserMessageId: null,
    httpCompleted: false,
    finalReceived: false,
    realtimeCompleted: false,
  });
  const {
    processingStages,
    addStage,
    completeActiveStage,
    errorActiveStage,
    clearStages,
  } = useProcessingStages();
  const [autoScrollRequest, setAutoScrollRequest] = useState(0);
  const [assistantName, setAssistantName] = useState("");
  const { toasts, addToast, dismissToast } = useToasts();
  const [activeFiles, setActiveFiles] = useState<string[]>([]);
  const [availableFiles, setAvailableFiles] = useState<IndexedFile[]>([]);
  const [fileRefreshKey, setFileRefreshKey] = useState(0);
  const [uploadProgress, setUploadProgress] = useState<UploadProgressData | null>(null);
  const [commandHistory, setCommandHistory] = useState<string[]>(
    () => getUiSetting("command_history") ?? []
  );
  const [ttsAvailable, setTtsAvailable] = useState(false);
  const [voiceMode, setVoiceMode] = useState(() => getUiSetting("voice_mode") === true);
  const [bridgeProviders, setBridgeProviders] = useState<ChatBridgeProviderItem[]>([]);
  const [bridgeProvidersAutoAttempted, setBridgeProvidersAutoAttempted] =
    useState(false);
  const [bridgeProvidersLoaded, setBridgeProvidersLoaded] = useState(false);
  const [bridgeProvidersLoading, setBridgeProvidersLoading] = useState(false);
  const [bridgeLinksByConversationId, setBridgeLinksByConversationId] = useState<
    Record<string, ChatBridgeLink>
  >({});
  const [bridgeActionLoadingProvider, setBridgeActionLoadingProvider] = useState<
    string | null
  >(null);
  const [showBridgeReauth, setShowBridgeReauth] = useState(false);
  const [pendingBridgeAction, setPendingBridgeAction] =
    useState<PendingBridgeAction | null>(null);
  const [pendingBridgeSettingsSave, setPendingBridgeSettingsSave] =
    useState<PendingBridgeSettingsSave | null>(null);
  const [showBridgeDetails, setShowBridgeDetails] = useState(false);
  const voiceModeRef = useRef(voiceMode);
  const previousRealtimeStatusRef = useRef<ConnectionStatus>("disconnected");
  const { confirm, ConfirmDialog } = useConfirm();

  const beginAssistantStream = useCallback(
    (
      assistantMessageId: string,
      options?: {
        conversationId?: string;
        voiceUserMessageId?: string;
      }
    ) => {
      pseudoStreamRunIdRef.current += 1;
      assistantMessageIdRef.current = assistantMessageId;
      streamedContentRef.current = "";
      streamedThinkingRef.current = "";
      pendingConfirmationsRef.current = [];
      activeAssistantStreamRef.current = {
        assistantMessageId,
        conversationId: options?.conversationId ?? null,
        requestId: null,
        runId: null,
        voiceUserMessageId: options?.voiceUserMessageId ?? null,
        httpCompleted: false,
        finalReceived: false,
        realtimeCompleted: false,
      };
    },
    []
  );

  const clearAssistantStream = useCallback(() => {
    pseudoStreamRunIdRef.current += 1;
    assistantMessageIdRef.current = null;
    streamedContentRef.current = "";
    streamedThinkingRef.current = "";
    activeAssistantStreamRef.current = {
      assistantMessageId: null,
      conversationId: null,
      requestId: null,
      runId: null,
      voiceUserMessageId: null,
      httpCompleted: false,
      finalReceived: false,
      realtimeCompleted: false,
    };
  }, []);

  const finalizeAssistantStreamIfReady = useCallback(() => {
    const activeStream = activeAssistantStreamRef.current;
    if (!shouldFinalizeAssistantStream(activeStream)) {
      return;
    }
    clearAssistantStream();
  }, [clearAssistantStream]);

  const isCurrentAssistantStreamEvent = useCallback((event: RealtimeEvent) => {
    const activeStream = activeAssistantStreamRef.current;
    if (!activeStream.assistantMessageId) {
      return false;
    }
    const eventRequestId = typeof event.request_id === "string" ? event.request_id : "";
    return (
      !eventRequestId ||
      !activeStream.requestId ||
      eventRequestId === activeStream.requestId
    );
  }, []);

  const isAssistantStreamStopped = useCallback((assistantMessageId: string) => {
    return stoppedAssistantMessageIdsRef.current.has(assistantMessageId);
  }, []);

  const pseudoStreamSection = useCallback(
    async ({
      assistantMessageId,
      runId,
      stats,
      text,
      buildUpdate,
    }: PseudoStreamSectionInput) => {
      if (!text) {
        return;
      }
      const chunkSize = pseudo_stream_chunk_size(stats);
      let visibleText = "";
      for (let offset = 0; offset < text.length; offset += chunkSize) {
        if (pseudoStreamRunIdRef.current !== runId) {
          return;
        }
        visibleText += text.slice(offset, offset + chunkSize);
        updateMessage(assistantMessageId, buildUpdate(visibleText));
        if (offset + chunkSize < text.length) {
          await sleep(PSEUDO_STREAM_INTERVAL_MS);
        }
      }
    },
    [updateMessage]
  );

  const playStructuredAssistantResponse = useCallback(
    async ({
      assistantMessageId,
      stats,
      sections,
      pendingConfirmations,
      usedTools,
    }: StructuredAssistantPlaybackInput): Promise<string> => {
      const runId = pseudoStreamRunIdRef.current;
      updateMessage(assistantMessageId, {
        stats,
        pendingConfirmations,
        usedTools,
      });

      await pseudoStreamSection({
        assistantMessageId,
        runId,
        stats,
        text: sections.thinking,
        buildUpdate: (visibleThinking) => ({
          thinking: visibleThinking,
        }),
      });
      await pseudoStreamSection({
        assistantMessageId,
        runId,
        stats,
        text: sections.answer,
        buildUpdate: (visibleAnswer) => ({
          content: visibleAnswer,
        }),
      });
      if (pseudoStreamRunIdRef.current !== runId) {
        return "";
      }

      updateMessage(assistantMessageId, {
        content: sections.answer,
        thinking: sections.thinking || undefined,
        suggestions:
          sections.suggestions && sections.suggestions.length > 0
            ? sections.suggestions
            : undefined,
      });
      return sections.answer;
    },
    [pseudoStreamSection, updateMessage]
  );

  const applyAssistantResponsePayload = useCallback(
    async ({
      assistantMessageId,
      response,
    }: ApplyAssistantResponseInput): Promise<string> => {
      const structuredContent = response.structured_content;
      const finalConfirmations = (response.pending_confirmations ?? []).map((c) => ({
        ...c,
        status: "pending" as const,
      }));
      // Union with confirmations already shown from realtime `confirmation`
      // events, so the final payload never clears a card the user is looking at.
      const seenActionIds = new Set(finalConfirmations.map((c) => c.action_id));
      const pendingConfirmations = [
        ...finalConfirmations,
        ...pendingConfirmationsRef.current.filter(
          (c) => !seenActionIds.has(c.action_id)
        ),
      ];
      const usedTools = response.used_tools;
      const pendingInput = response.pending_input
        ? {
            ...response.pending_input,
            run_id: response.run_id || "",
          }
        : undefined;
      if (pendingInput?.run_id) {
        pendingInputRef.current = pendingInput;
      }
      const structuredContentForPseudoStream = should_use_structured_pseudo_stream(
        structuredContent,
        streamedContentRef.current,
        streamedThinkingRef.current,
        response.stream_enabled
      )
        ? structuredContent
        : null;
      const visibleAnswer = visible_assistant_answer(
        structuredContent,
        streamedContentRef.current
      );
      const finalText = structuredContentForPseudoStream
        ? await playStructuredAssistantResponse({
            assistantMessageId,
            stats: response.stats,
            sections: {
              thinking: structuredContentForPseudoStream.thinking || "",
              answer: visibleAnswer,
              suggestions: structuredContentForPseudoStream.suggestions,
            },
            pendingConfirmations:
              pendingConfirmations && pendingConfirmations.length > 0
                ? pendingConfirmations
                : undefined,
            usedTools,
          })
        : visibleAnswer;
      if (!structuredContentForPseudoStream) {
        const assistantMessageFields = buildAssistantMessageFields({
          fallbackContent: finalText,
          structuredContent,
          stats: response.stats,
        });
        updateMessage(
          assistantMessageId,
          buildAssistantMessageUpdate(
            {
              ...assistantMessageFields,
              pendingConfirmations:
                pendingConfirmations && pendingConfirmations.length > 0
                  ? pendingConfirmations
                  : undefined,
              usedTools,
              pendingInput,
              error: undefined,
            },
            streamedThinkingRef.current || structuredContent?.thinking || ""
          )
        );
      }
      return finalText;
    },
    [playStructuredAssistantResponse, updateMessage]
  );

  // Load available file names for @mention autocomplete
  const refreshAvailableFiles = useCallback(() => {
    fetchFiles()
      .then((files) => {
        setAvailableFiles(files);
        const validIds = new Set(files.map((file) => file.file_id));
        setActiveFiles((prev) => prev.filter((fileId) => validIds.has(fileId)));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshAvailableFiles();
  }, [refreshAvailableFiles]);

  const loadBridgeProvidersIfNeeded = useCallback(
    async (showError = true) => {
      if (bridgeProvidersLoading || bridgeProvidersLoaded) {
        return;
      }
      setBridgeProvidersLoading(true);
      try {
        setBridgeProviders(await fetchChatBridgeProviders());
        setBridgeProvidersLoaded(true);
      } catch {
        if (showError) {
          addToast({
            type: "error",
            message: t("chat.failedToLoadProviders"),
          });
        }
      } finally {
        setBridgeProvidersLoading(false);
      }
    },
    [addToast, bridgeProvidersLoaded, bridgeProvidersLoading, t]
  );

  useEffect(() => {
    if (!isReady || bridgeProvidersLoaded || bridgeProvidersAutoAttempted) {
      return;
    }
    const timer = window.setTimeout(() => {
      setBridgeProvidersAutoAttempted(true);
      void loadBridgeProvidersIfNeeded(false);
    }, 150);
    return () => window.clearTimeout(timer);
  }, [
    bridgeProvidersAutoAttempted,
    bridgeProvidersLoaded,
    isReady,
    loadBridgeProvidersIfNeeded,
  ]);

  const availableFileNames = availableFiles.map((file) => file.filename);
  const activeFileNames = activeFiles
    .map((fileId) => availableFiles.find((file) => file.file_id === fileId)?.filename)
    .filter((name): name is string => Boolean(name));

  // Check TTS availability on mount
  useEffect(() => {
    checkTtsStatus().then(setTtsAvailable);
  }, []);

  // Load assistant name from profile on mount
  useEffect(() => {
    apiRequest<{ profile: { assistant_name?: string } }>("/admin/profile")
      .then((data) => setAssistantName(data.profile?.assistant_name || ""))
      .catch(() => {});
  }, []);

  // Keep voiceMode ref in sync
  useEffect(() => {
    voiceModeRef.current = voiceMode;
    setUiSetting("voice_mode", voiceMode);
  }, [voiceMode]);

  const toggleVoiceMode = useCallback(() => {
    setVoiceMode((v) => !v);
  }, []);

  const applyBridgeLink = useCallback(
    (conversationId: string, bridgeLink: ChatBridgeLink) => {
      setBridgeLinksByConversationId((previousLinks) => ({
        ...previousLinks,
        [conversationId]: bridgeLink,
      }));
      updateConversation(conversationId, {
        bridgeProvider: bridgeProviderFromLink(bridgeLink),
      });
    },
    [updateConversation]
  );

  const applyBridgeLinkStatus = useCallback(
    (
      conversationId: string,
      bridgeLink: ChatBridgeLink,
      status: ChatBridgeLink["status"]
    ) => {
      applyBridgeLink(conversationId, {
        ...bridgeLink,
        status,
      });
    },
    [applyBridgeLink]
  );

  const removeStoredBridgeLink = useCallback((conversationId: string) => {
    setBridgeLinksByConversationId((previousLinks) => {
      const nextLinks = { ...previousLinks };
      delete nextLinks[conversationId];
      return nextLinks;
    });
  }, []);

  // Auto-TTS + auto-record for voice mode
  const playTtsAndAutoRecord = useCallback(
    async (text: string) => {
      if (!text || !voiceModeRef.current || !ttsAvailable) return;
      try {
        const blobUrl = await synthesizeSpeech(text);
        if (!voiceModeRef.current) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        const audio = new Audio(blobUrl);
        audio.addEventListener("ended", () => {
          URL.revokeObjectURL(blobUrl);
          if (voiceModeRef.current) {
            setTimeout(() => {
              if (voiceModeRef.current) {
                chatInputRef.current?.startVoiceRecording?.();
              }
            }, 500);
          }
        });
        audio.addEventListener("error", () => {
          URL.revokeObjectURL(blobUrl);
        });
        await audio.play();
      } catch {
        // TTS failed — don't break the flow
      }
    },
    [ttsAvailable]
  );

  const handleRealtimeEvent = useCallback(
    (event: RealtimeEvent) => {
      const eventPayload =
        event.data && typeof event.data === "object"
          ? (event.data as Record<string, unknown>)
          : null;

      // A paused turn resumes through a separate /confirm request, and the
      // resumed turn publishes its lifecycle events under that confirm
      // request's request_id — one this page never saw. The only stable
      // link between the original turn and its resume is the turn's run_id,
      // so while a run is paused the outcome events are matched on run_id:
      // the resume runs to its outcome before publishing it, the server
      // state is complete, and re-syncing from it is the same strategy the
      // runtime_bootstrap path uses.
      const pausedResumeClass =
        activeChatId !== null
          ? classifyPausedResumeEvent(event, pausedRunRef.current, {
              activeAssistantMessageId: assistantMessageIdRef.current,
              conversationId: activeChatId,
            })
          : null;
      if (pausedResumeClass !== null && activeChatId !== null) {
        if (pausedResumeClass === "activity") {
          updateConversation(activeChatId, {
            processingStatus: "active",
            processingStage: t("chat.processing"),
            processingStartedAt: new Date(),
            updatedAt: new Date(),
          });
          return;
        }
        if (
          event.type === "final" ||
          event.type === "failed" ||
          event.type === "cancelled"
        ) {
          pausedRunRef.current = null;
        }
        void refreshConversationMessages(activeChatId).finally(() => {
          updateConversation(activeChatId, {
            processingStatus: "idle",
            processingStage: "",
            processingStartedAt: undefined,
          });
        });
        return;
      }

      const isAssistantRunEvent =
        event.type === "token" ||
        event.type === "thinking" ||
        event.type === "tool_status" ||
        event.type === "confirmation" ||
        event.type === "final" ||
        event.type === "paused" ||
        event.type === "failed" ||
        event.type === "cancelled" ||
        event.type === "error" ||
        event.type === "done";
      if (isAssistantRunEvent && !isCurrentAssistantStreamEvent(event)) {
        return;
      }

      if (event.type === "runtime_bootstrap") {
        const bootstrap = eventPayload as RuntimeBootstrapPayload | null;
        const conversationId = bootstrap?.conversation_id || "";
        const pausedEvent = runtimeBootstrapPausedEvent(bootstrap);
        if (pausedEvent && conversationId) {
          if (pausedEvent.run_id) {
            pausedRunRef.current = {
              conversationId,
              runId: pausedEvent.run_id,
            };
          }
          const rawConfirmations = pausedEvent.payload?.pending_confirmations;
          const confirmations = Array.isArray(rawConfirmations)
            ? rawConfirmations
                .filter((item): item is Record<string, unknown> =>
                  Boolean(item && typeof item === "object")
                )
                .map((item) => ({
                  action_id: String(item.action_id || ""),
                  tool_name: String(item.tool_name || ""),
                  parameters:
                    item.parameters && typeof item.parameters === "object"
                      ? (item.parameters as Record<string, unknown>)
                      : {},
                  status: "pending" as const,
                  grant_capable: item.grant_capable === true,
                  url_host: typeof item.url_host === "string" ? item.url_host : null,
                }))
                .filter((item) => item.action_id)
            : [];
          if (confirmations.length > 0) {
            addMessageToConversation(conversationId, {
              id: `paused-${pausedEvent.run_id || "turn"}`,
              role: "assistant",
              content: "",
              pendingConfirmations: confirmations,
              timestamp: new Date(),
            });
          } else {
            const rawInput = pausedEvent.payload?.pending_input;
            if (rawInput && typeof rawInput === "object") {
              const item = rawInput as Record<string, unknown>;
              const pendingInput: PendingInput = {
                run_id: String(pausedEvent.run_id || ""),
                pending_input_id: String(item.pending_input_id || ""),
                question: String(item.question || ""),
                suggestions: Array.isArray(item.suggestions)
                  ? item.suggestions.map(String)
                  : [],
                expected_input_schema:
                  item.expected_input_schema &&
                  typeof item.expected_input_schema === "object"
                    ? (item.expected_input_schema as Record<string, unknown>)
                    : {},
                expires_at: String(item.expires_at || ""),
                cas_version: Number(item.cas_version || 0),
              };
              pendingInputRef.current = pendingInput;
              addMessageToConversation(conversationId, {
                id: `paused-${pausedEvent.run_id || "turn"}`,
                role: "assistant",
                content: pendingInput.question,
                suggestions: pendingInput.suggestions,
                pendingInput,
                timestamp: new Date(),
              });
            }
          }
          updateConversation(conversationId, {
            processingStatus: "idle",
            processingStage: "",
            processingStartedAt: undefined,
          });
          setIsLoading(false);
          clearStages();
          return;
        }
        if (!runtimeBootstrapHasTerminalEvent(bootstrap)) {
          return;
        }
        if (pausedRunRef.current?.conversationId === conversationId) {
          pausedRunRef.current = null;
        }
        updateConversation(conversationId, {
          processingStatus: "idle",
          processingStage: "",
          processingStartedAt: undefined,
        });
        setIsLoading(false);
        clearStages();
        // Reload persisted history. An un-decided confirmation is persisted
        // server-side and comes back attached to its message, so the reload
        // restores the card instead of wiping it.
        void refreshConversationMessages(conversationId).finally(() => {
          const activeStream = activeAssistantStreamRef.current;
          if (activeStream.conversationId === conversationId) {
            clearAssistantStream();
          }
        });
      } else if (event.type === "token" && assistantMessageIdRef.current) {
        const tokenEvent = eventPayload as TokenEventPayload | null;
        streamedContentRef.current += tokenEvent?.content || event.content || "";
        updateMessage(assistantMessageIdRef.current, {
          content: streamedContentRef.current,
        });
      } else if (event.type === "thinking" && assistantMessageIdRef.current) {
        const tokenEvent = eventPayload as TokenEventPayload | null;
        const thinkingChunk = tokenEvent?.content || event.content || "";
        const isFirstThinkingChunk = streamedThinkingRef.current.length === 0;
        streamedThinkingRef.current += tokenEvent?.content || event.content || "";
        if (isFirstThinkingChunk && streamedContentRef.current) {
          streamedContentRef.current = "";
          updateMessage(assistantMessageIdRef.current, {
            content: "",
            thinking: thinkingChunk,
          });
        } else {
          updateMessage(assistantMessageIdRef.current, {
            thinking: streamedThinkingRef.current,
          });
        }
      } else if (event.type === "tool_status") {
        const status = eventPayload as ToolStatusEventPayload | null;
        if (status?.status === "started") {
          const toolName = status.name || "tool";
          const label = TOOL_LABELS[toolName] || `Running ${toolName}...`;
          addStage(label);
        } else if (status?.status === "completed") {
          completeActiveStage();
        } else if (status?.status === "error") {
          errorActiveStage();
        }
      } else if (event.type === "confirmation" && assistantMessageIdRef.current) {
        // A RED tool needs the user's approval before it runs. The backend
        // routes it as a realtime `confirmation` event; render it as a card on
        // the active assistant message.
        const conf = eventPayload as {
          action_id?: string;
          tool_name?: string;
          parameters?: Record<string, unknown>;
          grant_capable?: boolean;
          url_host?: string | null;
        } | null;
        if (conf?.action_id && conf.tool_name) {
          pendingConfirmationsRef.current = [
            ...pendingConfirmationsRef.current,
            {
              action_id: conf.action_id,
              tool_name: conf.tool_name,
              parameters: conf.parameters ?? {},
              status: "pending",
              grant_capable: conf.grant_capable === true,
              url_host: typeof conf.url_host === "string" ? conf.url_host : null,
            },
          ];
          updateMessage(assistantMessageIdRef.current, {
            pendingConfirmations: pendingConfirmationsRef.current,
          });
        }
      } else if (event.type === "paused" && assistantMessageIdRef.current) {
        const pausedPayload = eventPayload as ChatFinalEventPayload | null;
        const confirmations = (pausedPayload?.pending_confirmations ?? []).map((c) => ({
          ...c,
          status: "pending" as const,
        }));
        pendingConfirmationsRef.current = confirmations;
        const pendingInput = pausedPayload?.pending_input
          ? {
              ...pausedPayload.pending_input,
              run_id: pausedPayload.run_id || "",
            }
          : undefined;
        if (pendingInput?.run_id) {
          pendingInputRef.current = pendingInput;
        }
        updateMessage(assistantMessageIdRef.current, {
          pendingConfirmations: confirmations,
          pendingInput,
          content: pendingInput?.question || "",
          suggestions: pendingInput?.suggestions,
        });
        activeAssistantStreamRef.current.finalReceived = true;
        activeAssistantStreamRef.current.realtimeCompleted = true;
        setIsLoading(false);
        clearStages();
        const pausedConversationId = activeAssistantStreamRef.current.conversationId;
        if (pausedPayload?.run_id && pausedConversationId) {
          pausedRunRef.current = {
            conversationId: pausedConversationId,
            runId: pausedPayload.run_id,
          };
        }
      } else if (event.type === "cancelled") {
        pendingInputRef.current = null;
        const activeStream = activeAssistantStreamRef.current;
        if (activeStream.assistantMessageId) {
          updateMessage(activeStream.assistantMessageId, {
            error: "Request cancelled.",
          });
        }
        activeAssistantStreamRef.current = markStreamTerminalError(
          activeAssistantStreamRef.current
        );
        setIsLoading(false);
        clearStages();
      } else if (event.type === "done") {
        setIsLoading(false);
        setTimeout(() => clearStages(), 1000);
        activeAssistantStreamRef.current.realtimeCompleted = true;
        finalizeAssistantStreamIfReady();
      } else if (event.type === "final" || event.type === "failed") {
        pendingInputRef.current = null;
        const finalPayload = eventPayload as ChatFinalEventPayload | null;
        const activeStream = activeAssistantStreamRef.current;
        const assistantMessageId = activeStream.assistantMessageId;
        if (!assistantMessageId || !finalPayload) {
          return;
        }
        const finalizationId = finalPayload.finalization_id || null;
        if (finalizationId && processedFinalizationIdsRef.current.has(finalizationId)) {
          return;
        }
        if (finalizationId) {
          processedFinalizationIdsRef.current.add(finalizationId);
        }
        void (async () => {
          if (
            finalPayload.transcription &&
            activeStream.voiceUserMessageId &&
            activeStream.conversationId
          ) {
            updateMessage(activeStream.voiceUserMessageId, {
              content: finalPayload.transcription,
              isVoice: true,
            });
            updateConversation(activeStream.conversationId, {
              title: nextConversationTitle(
                activeChat?.title,
                finalPayload.transcription
              ),
            });
          }
          const finalText = await applyAssistantResponsePayload({
            assistantMessageId,
            response: finalPayload,
          });
          if (event.type === "failed" && finalPayload.error) {
            updateMessage(assistantMessageId, {
              error: finalPayload.error.message,
              errorCategory: finalPayload.error.code,
            });
          }
          activeAssistantStreamRef.current.finalReceived = true;
          activeAssistantStreamRef.current.realtimeCompleted = true;
          setIsLoading(false);
          setTimeout(() => clearStages(), 1000);
          finalizeAssistantStreamIfReady();
          if (voiceModeRef.current && finalText) {
            playTtsAndAutoRecord(finalText);
          }
        })().catch((error) => {
          if (finalizationId) {
            processedFinalizationIdsRef.current.delete(finalizationId);
          }
          addToast({
            type: "error",
            message:
              error instanceof Error
                ? error.message
                : t("chat.failedToApplyStreamedResponse"),
          });
        });
      } else if (event.type === "error") {
        const errorPayload = eventPayload as ChatErrorEventPayload | null;
        const activeStream = activeAssistantStreamRef.current;
        const assistantMessageId = activeStream.assistantMessageId;
        const message = resolveStreamErrorMessage(errorPayload?.message);
        if (assistantMessageId) {
          updateMessage(assistantMessageId, {
            content: streamedContentRef.current || "",
            error: message,
          });
        }
        activeAssistantStreamRef.current = markStreamTerminalError(
          activeAssistantStreamRef.current
        );
        setIsLoading(false);
        clearStages();
        if (activeStream.conversationId) {
          updateConversation(activeStream.conversationId, {
            processingStatus: "idle",
            processingStage: "",
            processingStartedAt: undefined,
          });
        }
      } else if (event.type === "bridge_inbound_message") {
        const inboundEvent = eventPayload as BridgeInboundMessageEventPayload | null;
        const conversationId = inboundEvent?.conversation_id || "";
        const inboundMessage = inboundEvent?.message;
        if (!conversationId || !inboundMessage?.id || !inboundMessage.content) {
          return;
        }
        const rawContent = inboundMessage.content;
        const messageTimestamp = inboundMessage.created_at
          ? new Date(inboundMessage.created_at)
          : new Date();
        addMessageToConversation(conversationId, {
          id: inboundMessage.id,
          role: inboundMessage.role || "user",
          content: extractMessageText(rawContent),
          images: extractMessageImages(rawContent),
          timestamp: messageTimestamp,
        });
        updateConversationActivity(conversationId, messageTimestamp);
      } else if (event.type === "bridge_link_updated") {
        const bridgeEvent = eventPayload as BridgeLinkUpdatedEventPayload | null;
        const conversationId = bridgeEvent?.conversation_id || "";
        const bridgeLink = bridgeEvent?.bridge_link;
        if (!conversationId || !bridgeLink) {
          return;
        }
        applyBridgeLink(conversationId, bridgeLink);
      } else if (event.type === "conversation_updated") {
        const conversationEvent =
          eventPayload as ConversationUpdatedEventPayload | null;
        const conversationId = conversationEvent?.conversation_id || "";
        if (!conversationId) {
          return;
        }
        updateConversation(conversationId, {
          title: conversationEvent?.title,
          bridgeProvider:
            conversationEvent?.bridge_provider === undefined
              ? undefined
              : conversationEvent.bridge_provider,
          settings: conversationEvent?.settings
            ? {
                showThinking: conversationEvent.settings.show_thinking ?? false,
                showSuggestions: conversationEvent.settings.show_suggestions ?? true,
                thinkingEnabled: conversationEvent.settings.thinking_enabled ?? true,
              }
            : undefined,
          processingStatus: conversationEvent?.processing_status,
          processingStage: conversationEvent?.processing_stage,
          processingStartedAt:
            conversationEvent?.processing_started_at === undefined
              ? undefined
              : conversationEvent.processing_started_at
                ? new Date(conversationEvent.processing_started_at)
                : null,
          updatedAt: conversationEvent?.updated_at
            ? new Date(conversationEvent.updated_at)
            : undefined,
        });
      } else if (event.type === "conversation_deleted") {
        const conversationEvent =
          eventPayload as ConversationDeletedEventPayload | null;
        const conversationId = conversationEvent?.conversation_id || "";
        if (!conversationId) {
          return;
        }
        removeConversation(conversationId);
      } else if (event.type === "conversation_created") {
        const conversationEvent =
          eventPayload as ConversationCreatedEventPayload | null;
        const conversationId = conversationEvent?.conversation_id || "";
        if (!conversationId) {
          return;
        }
        addOrUpdateConversation({
          id: conversationId,
          title: conversationEvent?.title || DEFAULT_CHAT_TITLE,
          bridgeProvider: conversationEvent?.bridge_provider || null,
          processingStatus: conversationEvent?.processing_status || "idle",
          processingStage: conversationEvent?.processing_stage || "",
          processingStartedAt: conversationEvent?.processing_started_at
            ? new Date(conversationEvent.processing_started_at)
            : undefined,
          createdAt: conversationEvent?.created_at
            ? new Date(conversationEvent.created_at)
            : new Date(),
          updatedAt: conversationEvent?.updated_at
            ? new Date(conversationEvent.updated_at)
            : new Date(),
        });
      } else if (event.type === "scheduled_task_result") {
        const taskName = String(eventPayload?.task_name || t("chat.scheduledTask"));
        const taskType = String(eventPayload?.task_type || "");
        const resultText = String(eventPayload?.result_text || "");
        const status = String(eventPayload?.status || "ok");
        const isError = status === "error";
        const summary =
          resultText.length > 200 ? resultText.slice(0, 200) + "..." : resultText;
        addToast({
          type: isError ? "error" : "success",
          message:
            taskType === "reminder"
              ? `🔔 ${taskName}: ${summary}`
              : `✅ ${taskName}: ${summary}`,
        });
        // The scheduler writes the run's messages to its own conversation
        // server-side; the browser gets no per-message push for it. If that
        // conversation is the one being viewed, refetch it so the result
        // appears immediately instead of only on the next socket reconnect.
        const scheduledConversationId = String(eventPayload?.conversation_id || "");
        if (scheduledConversationId && scheduledConversationId === activeChatId) {
          void refreshConversationMessages(scheduledConversationId);
          updateConversationActivity(scheduledConversationId, new Date());
        }
      }
    },
    [
      addOrUpdateConversation,
      addMessageToConversation,
      addToast,
      t,
      applyBridgeLink,
      updateConversation,
      removeConversation,
      updateMessage,
      setIsLoading,
      addStage,
      completeActiveStage,
      errorActiveStage,
      clearStages,
      finalizeAssistantStreamIfReady,
      refreshConversationMessages,
      clearAssistantStream,
      isCurrentAssistantStreamEvent,
      applyAssistantResponsePayload,
      activeChat?.title,
      activeChatId,
      playTtsAndAutoRecord,
      updateConversationActivity,
    ]
  );

  const {
    status: realtimeStatus,
    connect,
    disconnect,
  } = useRealtimeStream(handleRealtimeEvent, activeChatId);

  // Connect realtime on mount
  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  const handleCommandClick = useCallback((command: string) => {
    chatInputRef.current?.setValue(command);
    chatInputRef.current?.focus();
  }, []);

  const requestAutoScrollToBottom = useCallback(() => {
    setAutoScrollRequest((value) => value + 1);
  }, []);

  const replaceableUploadFiles = useCallback(
    async (documentFiles: File[]): Promise<File[]> => {
      const filesToUpload: File[] = [];
      for (const file of documentFiles) {
        const fileAlreadyExists = availableFiles.some(
          (item) => item.filename === file.name
        );
        if (!fileAlreadyExists) {
          filesToUpload.push(file);
          continue;
        }
        const shouldReplace = await confirm({
          title: t("chat.fileAlreadyExists"),
          description: t("chat.fileAlreadyExistsDescription", { name: file.name }),
          confirmLabel: t("chat.replace"),
          cancelLabel: t("chat.skip"),
        });
        if (shouldReplace) {
          filesToUpload.push(file);
        }
      }
      return filesToUpload;
    },
    [availableFiles, confirm, t]
  );

  const applyUploadedFileToActiveSelection = useCallback(
    (uploadedFileId: string, uploadedFilename: string) => {
      setActiveFiles((previousFileIds) => {
        const filteredFileIds = previousFileIds.filter((fileId) => {
          const existingFile = availableFiles.find((item) => item.file_id === fileId);
          return existingFile?.filename !== uploadedFilename;
        });
        if (filteredFileIds.includes(uploadedFileId)) {
          return filteredFileIds;
        }
        return [...filteredFileIds, uploadedFileId];
      });
    },
    [availableFiles]
  );

  const uploadDocumentFiles = useCallback(
    async (documentFiles: File[], baseMessageContent: string): Promise<string> => {
      const filesToUpload = await replaceableUploadFiles(documentFiles);
      if (filesToUpload.length === 0) {
        return baseMessageContent;
      }

      const results: Array<{ name: string; ok: boolean }> = [];

      setUploadProgress({
        current: 1,
        total: filesToUpload.length,
        filename: filesToUpload[0].name,
        results: [],
      });

      for (const [index, file] of filesToUpload.entries()) {
        setUploadProgress({
          current: index + 1,
          total: filesToUpload.length,
          filename: file.name,
          results: [...results],
        });
        try {
          const uploadResult = await uploadFile(file);
          results.push({ name: file.name, ok: true });
          applyUploadedFileToActiveSelection(
            uploadResult.file_id,
            uploadResult.filename
          );
          addToast({
            type: "success",
            message: t("chat.fileUploaded", { name: file.name }),
          });
        } catch (error) {
          const errorMessage =
            error instanceof Error ? error.message : t("chat.unknownError");
          results.push({ name: file.name, ok: false });
          addToast({
            type: "error",
            message: t("chat.failedToUploadFile", {
              name: file.name,
              error: errorMessage,
            }),
          });
        }
      }

      setUploadProgress({
        current: filesToUpload.length,
        total: filesToUpload.length,
        filename: filesToUpload[filesToUpload.length - 1].name,
        results: [...results],
      });
      setTimeout(() => setUploadProgress(null), 3000);
      setFileRefreshKey((value) => value + 1);
      refreshAvailableFiles();
      return baseMessageContent;
    },
    [
      addToast,
      t,
      applyUploadedFileToActiveSelection,
      refreshAvailableFiles,
      replaceableUploadFiles,
    ]
  );

  const sendAndStream = async (
    messageContent: string,
    conversationId: string,
    images?: string[],
    forcedTools?: string[],
    pendingInput?: PendingInput | null
  ) => {
    const assistantMessageId = generateId();
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      timestamp: new Date(),
    };
    addMessage(assistantMessage);
    beginAssistantStream(assistantMessageId, { conversationId });

    setIsLoading(true);
    clearStages();
    addStage(t("chat.sending"));
    let streamStarted = false;

    try {
      let body: Record<string, unknown> = {
        message: messageContent,
        conversation_id: conversationId,
      };
      if (images && images.length > 0) {
        body.images = images;
      }
      if (activeFiles.length > 0) {
        body.file_ids = activeFiles;
      }
      if (forcedTools && forcedTools.length > 0) body.forced_tools = forcedTools;
      if (pendingInput) {
        body = bindPendingInputReply(body, pendingInput, messageContent);
      }
      const response = await apiRequest<ChatResponsePayload>("/chat", {
        method: "POST",
        body: JSON.stringify(body),
      });
      // A new turn supersedes any pending resume of an earlier turn.
      pausedRunRef.current = null;
      if (response.outcome_kind !== "paused") {
        pendingInputRef.current = null;
      }

      if (isAssistantStreamStopped(assistantMessageId)) {
        stoppedAssistantMessageIdsRef.current.delete(assistantMessageId);
        updateConversation(conversationId, {
          processingStatus: "idle",
          processingStage: "",
          processingStartedAt: undefined,
        });
        return;
      }

      if (response.stream_started) {
        streamStarted = true;
        activeAssistantStreamRef.current.requestId = response.request_id || null;
        activeAssistantStreamRef.current.runId = response.run_id || null;
        activeAssistantStreamRef.current.httpCompleted = true;
        finalizeAssistantStreamIfReady();
        return;
      }

      const streamFinalAlreadyApplied = activeAssistantStreamRef.current.finalReceived;
      const finalText = !shouldApplyHttpChatResponse(streamFinalAlreadyApplied)
        ? visible_assistant_answer(
            response.structured_content,
            streamedContentRef.current
          )
        : await applyAssistantResponsePayload({
            assistantMessageId,
            response,
          });
      activeAssistantStreamRef.current.httpCompleted = true;
      finalizeAssistantStreamIfReady();

      // Voice mode: auto-play TTS
      if (!streamFinalAlreadyApplied && voiceModeRef.current && finalText) {
        playTtsAndAutoRecord(finalText);
      }

      updateConversation(conversationId, {
        processingStatus: "idle",
        processingStage: "",
        processingStartedAt: undefined,
      });
    } catch (err) {
      if (isAssistantStreamStopped(assistantMessageId)) {
        stoppedAssistantMessageIdsRef.current.delete(assistantMessageId);
        updateConversation(conversationId, {
          processingStatus: "idle",
          processingStage: "",
          processingStartedAt: undefined,
        });
        return;
      }
      const keepStreamedResponse = shouldKeepStreamedAssistantResponseAfterRequestError(
        err,
        streamedContentRef.current,
        streamedThinkingRef.current
      );
      updateMessage(
        assistantMessageId,
        buildAssistantMessageUpdate(
          {
            content: streamedContentRef.current || "",
            error: keepStreamedResponse
              ? undefined
              : err instanceof Error
                ? err.message
                : t("chat.failedToSendMessage"),
          },
          streamedThinkingRef.current
        )
      );
      if (keepStreamedResponse) {
        activeAssistantStreamRef.current.httpCompleted = true;
        finalizeAssistantStreamIfReady();
      } else {
        clearAssistantStream();
      }
      updateConversation(conversationId, {
        processingStatus: "idle",
        processingStage: "",
        processingStartedAt: undefined,
      });
    } finally {
      if (!streamStarted) {
        setIsLoading(false);
        clearStages();
        requestAnimationFrame(() => chatInputRef.current?.focus());
      }
    }
  };

  const handleSend = async (
    content: string,
    files?: File[],
    forcedTools?: string[]
  ) => {
    // Save to command history
    setCommandHistory((prev) => {
      const next = [content, ...prev.filter((h) => h !== content)].slice(
        0,
        COMMAND_HISTORY_LIMIT
      );
      setUiSetting("command_history", next);
      return next;
    });

    const currentChat = await ensureActiveChat();
    if (!currentChat) return;

    const { imageFiles, documentFiles } = splitAttachedFiles(files || []);

    const imageDataUrls = await Promise.all(
      imageFiles.map((file) => imageFileDataUrl(file))
    );

    let messageContent = content;
    if (documentFiles.length > 0) {
      messageContent = await uploadDocumentFiles(documentFiles, content);
    }

    // Add user message
    const userMessage: Message = {
      id: generateId(),
      role: "user",
      content: messageContent,
      images: imageDataUrls.length > 0 ? imageDataUrls : undefined,
      timestamp: new Date(),
    };
    updateConversation(currentChat.id, {
      title: nextConversationTitle(currentChat.title, messageContent),
      processingStatus: "active",
      processingStage: t("chat.processing"),
      processingStartedAt: userMessage.timestamp,
      updatedAt: userMessage.timestamp,
    });
    requestAutoScrollToBottom();
    addMessage(userMessage);

    const pendingInput = pendingInputRef.current;
    await sendAndStream(
      messageContent,
      currentChat.id,
      imageDataUrls.length > 0 ? imageDataUrls : undefined,
      forcedTools,
      pendingInput
    );
  };

  const handleEditMessage = async (messageId: string, newContent: string) => {
    updateMessage(messageId, { content: newContent });
    truncateAfterMessage(messageId);
    requestAutoScrollToBottom();
    const ensuredChat = await ensureActiveChat();
    const conversationId = activeChatId ?? ensuredChat?.id;
    if (!conversationId) return;
    updateConversation(conversationId, {
      processingStatus: "active",
      processingStage: t("chat.processing"),
      processingStartedAt: new Date(),
      updatedAt: new Date(),
    });
    await sendAndStream(newContent, conversationId);
  };

  const handleSendVoice = async (voiceDataUrl: string) => {
    const currentChat = await ensureActiveChat();
    if (!currentChat) return;

    // Add user message with voice indicator and audio for playback
    const userMessage: Message = {
      id: generateId(),
      role: "user",
      content: "",
      isVoice: true,
      voiceDataUrl,
      timestamp: new Date(),
    };
    updateConversation(currentChat.id, {
      processingStatus: "active",
      processingStage: t("chat.transcribing"),
      processingStartedAt: userMessage.timestamp,
      updatedAt: userMessage.timestamp,
    });
    requestAutoScrollToBottom();
    addMessage(userMessage);

    // Send voice to backend
    const assistantMessageId = generateId();
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      timestamp: new Date(),
    };
    addMessage(assistantMessage);
    beginAssistantStream(assistantMessageId, {
      conversationId: currentChat.id,
      voiceUserMessageId: userMessage.id,
    });

    setIsLoading(true);
    clearStages();
    addStage(t("chat.transcribing"));
    let streamStarted = false;

    try {
      const body: Record<string, unknown> = {
        voice: voiceDataUrl,
        conversation_id: currentChat.id,
      };
      if (activeFiles.length > 0) {
        body.file_ids = activeFiles;
      }
      const response = await apiRequest<ChatResponsePayload>("/chat", {
        method: "POST",
        body: JSON.stringify(body),
      });
      // A new turn supersedes any pending resume of an earlier turn.
      pausedRunRef.current = null;

      if (isAssistantStreamStopped(assistantMessageId)) {
        stoppedAssistantMessageIdsRef.current.delete(assistantMessageId);
        updateConversation(currentChat.id, {
          processingStatus: "idle",
          processingStage: "",
          processingStartedAt: undefined,
        });
        return;
      }

      if (response.stream_started) {
        streamStarted = true;
        activeAssistantStreamRef.current.requestId = response.request_id || null;
        activeAssistantStreamRef.current.runId = response.run_id || null;
        activeAssistantStreamRef.current.httpCompleted = true;
        finalizeAssistantStreamIfReady();
        return;
      }

      // Update user message with transcription
      if (response.transcription) {
        updateMessage(userMessage.id, {
          content: response.transcription,
          isVoice: true,
        });
        updateConversation(currentChat.id, {
          title: nextConversationTitle(currentChat.title, response.transcription),
        });
      }

      const streamFinalAlreadyApplied = activeAssistantStreamRef.current.finalReceived;
      const finalText = !shouldApplyHttpChatResponse(streamFinalAlreadyApplied)
        ? visible_assistant_answer(
            response.structured_content,
            streamedContentRef.current
          )
        : await applyAssistantResponsePayload({
            assistantMessageId,
            response,
          });
      activeAssistantStreamRef.current.httpCompleted = true;
      finalizeAssistantStreamIfReady();

      // Voice mode: auto-play TTS
      if (!streamFinalAlreadyApplied && voiceModeRef.current && finalText) {
        playTtsAndAutoRecord(finalText);
      }

      updateConversation(currentChat.id, {
        processingStatus: "idle",
        processingStage: "",
        processingStartedAt: undefined,
      });
    } catch (err) {
      if (isAssistantStreamStopped(assistantMessageId)) {
        stoppedAssistantMessageIdsRef.current.delete(assistantMessageId);
        updateConversation(currentChat.id, {
          processingStatus: "idle",
          processingStage: "",
          processingStartedAt: undefined,
        });
        return;
      }
      const keepStreamedResponse = shouldKeepStreamedAssistantResponseAfterRequestError(
        err,
        streamedContentRef.current,
        streamedThinkingRef.current
      );
      updateMessage(
        assistantMessageId,
        buildAssistantMessageUpdate(
          {
            content: streamedContentRef.current || "",
            error: keepStreamedResponse
              ? undefined
              : err instanceof Error
                ? err.message
                : t("chat.failedToSendVoiceMessage"),
          },
          streamedThinkingRef.current
        )
      );
      if (keepStreamedResponse) {
        activeAssistantStreamRef.current.httpCompleted = true;
        finalizeAssistantStreamIfReady();
      } else {
        clearAssistantStream();
      }
      updateConversation(currentChat.id, {
        processingStatus: "idle",
        processingStage: "",
        processingStartedAt: undefined,
      });
    } finally {
      if (!streamStarted) {
        setIsLoading(false);
        clearStages();
        requestAnimationFrame(() => chatInputRef.current?.focus());
      }
    }
  };

  // The pinned panel shows the oldest still-pending confirmation (message
  // order is chronological, so the first hit wins); with nothing pending,
  // a recently expired confirmation on the latest message explains why
  // the card stopped waiting.
  const activePendingConfirmation = useMemo(() => {
    const messages = activeChat?.messages ?? [];
    for (const message of messages) {
      for (const item of message.pendingConfirmations ?? []) {
        if (item.status === "pending") {
          return { messageId: message.id, confirmation: item, actionable: true };
        }
      }
    }
    const lastMessage = messages[messages.length - 1];
    const expiredItems = (lastMessage?.pendingConfirmations ?? []).filter(
      (item) => item.status === "expired"
    );
    const lastExpired = expiredItems[expiredItems.length - 1];
    if (lastMessage && lastExpired) {
      return {
        messageId: lastMessage.id,
        confirmation: lastExpired,
        actionable: false,
      };
    }
    return null;
  }, [activeChat]);

  const handleConfirmationDecision = useCallback(
    async (decision: "approve" | "deny", scope: ConfirmationScope) => {
      if (!activePendingConfirmation?.actionable || confirmBusy) return;
      const { messageId, confirmation } = activePendingConfirmation;
      // Captured at decision time so a chat switch mid-request cannot
      // redirect the status patch or the trace line into another chat.
      const conversationId = activeChatId;
      const hostMessage = activeChat?.messages.find((m) => m.id === messageId);
      if (!conversationId || !hostMessage) return;
      const patchStatus = (status: PendingConfirmation["status"]) => {
        updateMessageInConversation(conversationId, messageId, {
          pendingConfirmations: (hostMessage.pendingConfirmations ?? []).map((item) =>
            item.action_id === confirmation.action_id ? { ...item, status } : item
          ),
        });
        pendingConfirmationsRef.current = pendingConfirmationsRef.current.map((item) =>
          item.action_id === confirmation.action_id ? { ...item, status } : item
        );
      };
      setConfirmBusy(true);
      try {
        const response =
          decision === "approve"
            ? await confirmAction(confirmation.action_id, scope, conversationId)
            : await denyAction(confirmation.action_id, conversationId);
        const failedOutcome =
          !response.accepted ||
          response.status === "failed" ||
          response.status === "cancelled";
        if (failedOutcome) {
          // Expired/refused server-side: the confirmation is dead either way.
          patchStatus("expired");
          addToast({ type: "error", message: t("common.confirmStale") });
          return;
        }
        patchStatus(decision === "approve" ? "approved" : "denied");
        const traceKey =
          decision === "deny"
            ? "common.confirmTraceDenied"
            : scope === "domain"
              ? "common.confirmTraceAllowedDomain"
              : scope === "conversation"
                ? "common.confirmTraceAllowedConversation"
                : "common.confirmTraceAllowedOnce";
        addMessageToConversation(conversationId, {
          id: generateId(),
          role: "assistant",
          content: t(traceKey, {
            tool: confirmation.tool_name,
            host: confirmation.url_host ?? "",
          }),
          timestamp: new Date(),
        });
      } catch {
        // Transient transport failure: the server-side confirmation is
        // still valid — keep it pending so the user can simply retry.
        addToast({ type: "error", message: t("common.error") });
      } finally {
        setConfirmBusy(false);
      }
    },
    [
      activePendingConfirmation,
      activeChat,
      activeChatId,
      addMessageToConversation,
      addToast,
      confirmBusy,
      t,
      updateMessageInConversation,
    ]
  );

  const handleStop = async () => {
    const activeStream = activeAssistantStreamRef.current;
    const requestId = activeStream.requestId;
    const runId = activeStream.runId;
    if (activeStream.assistantMessageId) {
      stoppedAssistantMessageIdsRef.current.add(activeStream.assistantMessageId);
      updateMessage(activeStream.assistantMessageId, {
        content: streamedContentRef.current || t("chat.stopped"),
      });
    }
    if (activeStream.conversationId) {
      updateConversation(activeStream.conversationId, {
        processingStatus: "idle",
        processingStage: "",
        processingStartedAt: undefined,
      });
    }
    setIsLoading(false);
    clearStages();
    clearAssistantStream();
    requestAnimationFrame(() => chatInputRef.current?.focus());
    if (!requestId || !runId) return;
    try {
      await apiRequest("/stop", {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, run_id: runId }),
      });
    } catch {
      /* stop is best-effort */
    }
  };

  const handleNewChat = useCallback(() => {
    void createNewChat().then((chat) => {
      navigate(`/c/${chat.id}`);
    });
  }, [createNewChat, navigate]);

  const activeBridgeLink = activeChatId
    ? (bridgeLinksByConversationId[activeChatId] ?? null)
    : null;

  const activeBridgeProvider = activeChat?.bridgeProvider || null;

  const bridgeProvidersByChatId = bridgeProviderMap(chats);

  useEffect(() => {
    if (
      !showBridgeDetails ||
      !activeChatId ||
      activeBridgeLink ||
      !activeBridgeProvider
    ) {
      return;
    }
    let cancelled = false;
    fetchChatBridgeForConversation(activeChatId)
      .then((bridgeLink) => {
        if (cancelled || !bridgeLink) {
          return;
        }
        applyBridgeLink(activeChatId, bridgeLink);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [
    activeBridgeLink,
    applyBridgeLink,
    activeBridgeProvider,
    activeChatId,
    showBridgeDetails,
  ]);

  const handleToggleThinking = useCallback(async () => {
    if (!activeChatId) return;
    const next = !chatSettings.thinkingEnabled;
    updateConversation(activeChatId, {
      settings: { ...chatSettings, thinkingEnabled: next },
    });
    try {
      await updateChatSettings(activeChatId, { thinking_enabled: next });
    } catch {
      updateConversation(activeChatId, {
        settings: { ...chatSettings, thinkingEnabled: !next },
      });
    }
  }, [activeChatId, chatSettings, updateConversation]);

  const handleToggleSuggestions = useCallback(async () => {
    if (!activeChatId) return;
    const next = !showSuggestions;
    updateConversation(activeChatId, {
      settings: { ...chatSettings, showSuggestions: next },
    });
    try {
      await updateChatSettings(activeChatId, { show_suggestions: next });
    } catch {
      updateConversation(activeChatId, {
        settings: { ...chatSettings, showSuggestions: !next },
      });
    }
  }, [activeChatId, showSuggestions, chatSettings, updateConversation]);

  const handleBridgeToggleRequest = useCallback(
    async (provider: string, enabled: boolean) => {
      const currentChat = await ensureActiveChat();
      if (!currentChat) {
        return;
      }
      setPendingBridgeAction({
        conversationId: currentChat.id,
        provider,
        enabled,
      });
      setShowBridgeReauth(true);
    },
    [ensureActiveChat]
  );

  const handleBridgeDetailsOpen = useCallback(() => {
    setShowBridgeDetails(true);
    void loadBridgeProvidersIfNeeded();
  }, [loadBridgeProvidersIfNeeded]);

  const handleBridgeSettingsSave = useCallback(
    (settings: {
      deliveryMode: string;
      personaMode: string;
      personaUserId: string;
    }) => {
      if (!activeBridgeLink || !activeChatId) {
        return;
      }
      setPendingBridgeSettingsSave({
        bridgeLinkId: activeBridgeLink.id,
        conversationId: activeChatId,
        deliveryMode: settings.deliveryMode,
        personaMode: settings.personaMode,
        personaUserId: settings.personaUserId,
      });
      setShowBridgeReauth(true);
    },
    [activeBridgeLink, activeChatId]
  );

  const enableStoredBridgeLink = useCallback(
    async (conversationId: string, bridgeLink: ChatBridgeLink, reauthToken: string) => {
      if (bridgeLink.status !== "active") {
        await enableChatBridgeLink(bridgeLink.id, { reauthToken });
      }
      applyBridgeLinkStatus(conversationId, bridgeLink, "active");
    },
    [applyBridgeLinkStatus]
  );

  const createBridgeLinkForConversation = useCallback(
    async (
      conversationId: string,
      provider: string,
      title: string,
      reauthToken: string
    ) => {
      const providerItem = bridgeProviders.find((item) => item.provider === provider);
      const createdBridgeLink = await createChatBridgeLink(
        conversationId,
        provider,
        {
          reauthToken,
          ...bridgeCreateSettings(
            provider,
            providerItem?.default_persona_user_id || ""
          ),
        },
        title
      );
      applyBridgeLink(conversationId, createdBridgeLink);
    },
    [applyBridgeLink, bridgeProviders]
  );

  const disableStoredBridgeLink = useCallback(
    async (conversationId: string, bridgeLink: ChatBridgeLink, reauthToken: string) => {
      await disableChatBridgeLink(bridgeLink.id, { reauthToken });
      applyBridgeLinkStatus(conversationId, bridgeLink, "disabled");
    },
    [applyBridgeLinkStatus]
  );

  const handleBridgeReauthSuccess = useCallback(
    async (reauthToken?: string) => {
      setShowBridgeReauth(false);
      const pendingSettingsSave = pendingBridgeSettingsSave;
      const bridgeAction = pendingBridgeAction;
      if ((!bridgeAction && !pendingSettingsSave) || !reauthToken) {
        setPendingBridgeAction(null);
        setPendingBridgeSettingsSave(null);
        return;
      }
      setBridgeActionLoadingProvider(
        bridgeAction?.provider || activeBridgeProvider || DISCORD_BRIDGE_PROVIDER
      );
      try {
        if (pendingSettingsSave) {
          const updatedBridgeLink = await updateChatBridgeLinkSettings(
            pendingSettingsSave.bridgeLinkId,
            {
              reauthToken,
              deliveryMode: pendingSettingsSave.deliveryMode,
              personaMode: pendingSettingsSave.personaMode,
              personaUserId: pendingSettingsSave.personaUserId,
            }
          );
          if (updatedBridgeLink) {
            applyBridgeLink(pendingSettingsSave.conversationId, updatedBridgeLink);
          }
          addToast({
            type: "success",
            message: t("chat.bridgeSettingsSaved"),
          });
        } else if (bridgeAction) {
          const currentBridgeLink = await fetchChatBridgeForConversation(
            bridgeAction.conversationId
          );
          if (bridgeAction.enabled) {
            if (
              currentBridgeLink &&
              currentBridgeLink.provider === bridgeAction.provider
            ) {
              await enableStoredBridgeLink(
                bridgeAction.conversationId,
                currentBridgeLink,
                reauthToken
              );
            } else {
              if (currentBridgeLink && currentBridgeLink.status === "active") {
                await disableChatBridgeLink(currentBridgeLink.id, { reauthToken });
              }
              await createBridgeLinkForConversation(
                bridgeAction.conversationId,
                bridgeAction.provider,
                activeChat?.title || bridgeAction.conversationId,
                reauthToken
              );
            }
            addToast({
              type: "success",
              message: t("chat.bridgeConnected", { provider: bridgeAction.provider }),
            });
          } else {
            if (
              currentBridgeLink &&
              currentBridgeLink.provider === bridgeAction.provider &&
              currentBridgeLink.status === "active"
            ) {
              await disableStoredBridgeLink(
                bridgeAction.conversationId,
                currentBridgeLink,
                reauthToken
              );
              addToast({
                type: "success",
                message: t("chat.bridgeDisconnected", {
                  provider: bridgeAction.provider,
                }),
              });
            }
          }
        }
      } catch (error) {
        const message =
          error instanceof ApiError && error.status === 403
            ? t("chat.reauthenticationRequired")
            : error instanceof Error
              ? error.message
              : pendingSettingsSave
                ? t("chat.failedToSaveBridgeSettings")
                : t("chat.failedToUpdateBridge");
        addToast({
          type: "error",
          message,
        });
      } finally {
        setBridgeActionLoadingProvider(null);
        setPendingBridgeAction(null);
        setPendingBridgeSettingsSave(null);
      }
    },
    [
      activeBridgeProvider,
      activeChat?.title,
      addToast,
      t,
      applyBridgeLink,
      createBridgeLinkForConversation,
      disableStoredBridgeLink,
      enableStoredBridgeLink,
      pendingBridgeAction,
      pendingBridgeSettingsSave,
    ]
  );

  useEffect(() => {
    const previousRealtimeStatus = previousRealtimeStatusRef.current;
    previousRealtimeStatusRef.current = realtimeStatus;
    if (!isReady || realtimeStatus !== "connected") {
      return;
    }
    if (previousRealtimeStatus === "connected") {
      return;
    }
    void refreshConversations();
    if (!activeChatId || isLoading) {
      return;
    }
    void refreshConversationMessages(activeChatId);
  }, [
    activeChatId,
    isLoading,
    isReady,
    refreshConversations,
    refreshConversationMessages,
    realtimeStatus,
  ]);

  const isUploading = uploadProgress !== null;
  const backendRunActive = activeChat?.processingStatus === "active";
  const isDisabled = chatInputIsDisabled({
    backendRunActive,
    localRequestActive: isLoading,
    uploadActive: isUploading,
  });
  const serverProcessingStages =
    !isLoading && activeChat?.processingStatus === "active"
      ? buildServerProcessingStage(
          activeChat.processingStage,
          activeChat.processingStartedAt
        )
      : [];
  const displayedProcessingStages =
    isLoading || processingStages.length > 0
      ? processingStages
      : serverProcessingStages;
  // Label of the currently-active stage, shown as a shimmering overlay inside
  // the chat input (in place of the placeholder) while a request is processing.
  const lastProcessingStage =
    displayedProcessingStages.length > 0
      ? displayedProcessingStages[displayedProcessingStages.length - 1]
      : null;
  const currentProcessingStage =
    lastProcessingStage && lastProcessingStage.status === "active"
      ? {
          label: lastProcessingStage.label,
          startedAt: lastProcessingStage.startedAt,
        }
      : null;

  const sidebar = (
    <Sidebar
      chats={chats}
      bridgeProvidersByChatId={bridgeProvidersByChatId}
      activeChatId={activeChatId}
      onNewChat={handleNewChat}
      onSelectChat={(chatId) => {
        navigate(`/c/${chatId}`);
      }}
      onDeleteChat={(chatId) => {
        removeStoredBridgeLink(chatId);
        void deleteChat(chatId).then(() => {
          if (chatId === activeChatId) {
            navigate("/", { replace: true });
          }
        });
      }}
      onRenameChat={(chatId, newTitle) => {
        void renameChat(chatId, newTitle);
      }}
      onLogout={logout}
      activeFiles={activeFiles}
      onActiveFilesChange={setActiveFiles}
      onFilesChange={refreshAvailableFiles}
      fileRefreshKey={fileRefreshKey}
      voiceMode={voiceMode && ttsAvailable}
      onToggleVoiceMode={ttsAvailable ? toggleVoiceMode : undefined}
    />
  );

  return (
    <Layout
      sidebar={sidebar}
      header={
        <Header
          title={activeChat?.title || t("chat.newChat")}
          bridgeProvider={activeBridgeProvider}
          onOpenBridgeDetails={handleBridgeDetailsOpen}
        />
      }
    >
      <MessageList
        conversationKey={activeChatId}
        autoScrollRequest={autoScrollRequest}
        messages={activeChat?.messages ?? []}
        isLoading={isLoading}
        assistantName={assistantName}
        onCommandClick={handleCommandClick}
        onEditMessage={handleEditMessage}
        showThinking={showThinking}
        ttsAvailable={ttsAvailable}
      />
      {activePendingConfirmation && (
        <PendingConfirmationPanel
          confirmation={activePendingConfirmation.confirmation}
          onDecide={handleConfirmationDecision}
          busy={confirmBusy}
          actionable={activePendingConfirmation.actionable}
        />
      )}
      <ChatInput
        ref={chatInputRef}
        onSend={handleSend}
        onSendVoice={handleSendVoice}
        onAbort={handleStop}
        disabled={isDisabled}
        isLoading={isLoading}
        uploadProgress={uploadProgress}
        placeholder={
          realtimeStatus === "connecting"
            ? t("chat.connectingToServer")
            : realtimeStatus === "disconnected"
              ? t("chat.serverDisconnected")
              : t("chat.askAnything")
        }
        processingStage={currentProcessingStage}
        activeFiles={activeFileNames}
        onDeactivateFile={(filename) => {
          const match = availableFiles.find((file) => file.filename === filename);
          if (!match) return;
          setActiveFiles((prev) => prev.filter((fileId) => fileId !== match.file_id));
        }}
        availableFiles={availableFileNames}
        commandHistory={commandHistory}
        voiceMode={voiceMode && ttsAvailable}
        bridgeProviders={bridgeProviders.map(({ provider }) => ({
          provider,
          enabled: activeBridgeProvider === provider,
        }))}
        bridgeProvidersLoading={bridgeProvidersLoading}
        bridgeDisabled={isDisabled}
        bridgeLoadingProvider={bridgeActionLoadingProvider}
        onBridgeMenuOpenChange={(open) => {
          if (open) {
            void loadBridgeProvidersIfNeeded();
          }
        }}
        onToggleBridgeProvider={handleBridgeToggleRequest}
        showThinking={showThinking}
        onToggleThinking={handleToggleThinking}
        showSuggestions={showSuggestions}
        onToggleSuggestions={handleToggleSuggestions}
      />
      <ConfirmDialog />
      {showBridgeReauth ? (
        <ReauthModal
          onSuccess={handleBridgeReauthSuccess}
          onClose={() => {
            setShowBridgeReauth(false);
            setPendingBridgeAction(null);
            setPendingBridgeSettingsSave(null);
          }}
        />
      ) : null}
      {showBridgeDetails ? (
        <ChatBridgeDetailsModal
          key={activeBridgeLink?.id ?? "chat-bridge-details"}
          chatTitle={activeChat?.title || t("chat.newChat")}
          bridgeLink={activeBridgeLink}
          availableProviders={bridgeProviders}
          settingsSaving={bridgeActionLoadingProvider !== null}
          onSaveSettings={handleBridgeSettingsSave}
          onClose={() => setShowBridgeDetails(false)}
        />
      ) : null}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </Layout>
  );
}
