import { useEffect, useRef, useMemo, memo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChatMessage } from "./ChatMessage";
import type { Message, PendingConfirmation } from "@/types/chat";
import { cn } from "@/lib/utils";

const AUTO_SCROLL_THRESHOLD_PX = 80;

function scrollViewportToBottom(
  viewport: HTMLElement | null,
  behavior: ScrollBehavior
): void {
  if (!viewport) {
    return;
  }
  if (typeof viewport.scrollTo === "function") {
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior,
    });
    return;
  }
  viewport.scrollTop = viewport.scrollHeight;
}

function distanceFromBottom(viewport: HTMLElement): number {
  return viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop;
}

function isNearBottom(viewport: HTMLElement): boolean {
  return distanceFromBottom(viewport) <= AUTO_SCROLL_THRESHOLD_PX;
}

interface MessageListProps {
  conversationKey?: string | null;
  autoScrollRequest?: number;
  messages: Message[];
  isLoading?: boolean;
  className?: string;
  assistantName?: string;
  onCommandClick?: (command: string) => void;
  showSql?: boolean;
  showThinking?: boolean;
  onEditMessage?: (messageId: string, newContent: string) => void;
  ttsAvailable?: boolean;
  onConfirmationStatusChange?: (
    actionId: string,
    status: PendingConfirmation["status"]
  ) => void;
}

/**
 * Scrollable list of chat messages.
 * Memoized to prevent unnecessary re-renders during streaming.
 */
export const MessageList = memo(function MessageList({
  conversationKey,
  autoScrollRequest = 0,
  messages,
  isLoading = false,
  className,
  assistantName,
  onCommandClick,
  showSql = true,
  showThinking = true,
  onEditMessage,
  ttsAvailable = false,
  onConfirmationStatusChange,
}: MessageListProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef(true);

  const getViewport = () =>
    rootRef.current?.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]') ??
    null;

  // Find the last user message ID (for edit button visibility)
  const lastUserMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") return messages[i].id;
    }
    return null;
  }, [messages]);

  useEffect(() => {
    shouldStickToBottomRef.current = true;
    const frameId = window.requestAnimationFrame(() => {
      scrollViewportToBottom(getViewport(), "auto");
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [conversationKey]);

  useEffect(() => {
    if (autoScrollRequest === 0) {
      return;
    }
    shouldStickToBottomRef.current = true;
    scrollViewportToBottom(getViewport(), "auto");
  }, [autoScrollRequest]);

  useEffect(() => {
    const nextViewport = getViewport();
    if (!nextViewport) {
      return undefined;
    }

    const handleScroll = () => {
      shouldStickToBottomRef.current = isNearBottom(nextViewport);
    };

    nextViewport.addEventListener("scroll", handleScroll, { passive: true });
    return () => nextViewport.removeEventListener("scroll", handleScroll);
  }, [conversationKey]);

  useEffect(() => {
    if (!shouldStickToBottomRef.current) {
      return;
    }
    scrollViewportToBottom(getViewport(), "auto");
  }, [messages]);

  if (messages.length === 0 && !isLoading) {
    return (
      <>
        <div className={cn("flex-1", className)} />
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <Card className="pointer-events-auto w-full max-w-sm shadow-none">
            <CardContent className="flex flex-col items-center gap-2 p-6 text-center">
              <h2 className="text-xl font-semibold">biMEMO</h2>
              <p className="text-muted-foreground">Your personal AI assistant</p>
            </CardContent>
          </Card>
        </div>
      </>
    );
  }

  return (
    <ScrollArea
      ref={rootRef}
      className={cn("flex-1 min-h-0 message-scroll-area", className)}
    >
      {/* Container with max-width and horizontal padding for large screens */}
      <div className="mx-auto w-full max-w-[1920px] sm:px-4 2xl:px-[10%]">
        <div className="divide-y divide-border">
          {messages.map((message) => (
            <ChatMessage
              key={message.id}
              message={message}
              assistantName={assistantName}
              onCommandClick={onCommandClick}
              showSql={showSql}
              showThinking={showThinking}
              isEditable={message.id === lastUserMessageId && !isLoading}
              onEdit={onEditMessage}
              ttsAvailable={ttsAvailable}
              onConfirmationStatusChange={onConfirmationStatusChange}
            />
          ))}
        </div>
      </div>
    </ScrollArea>
  );
});
