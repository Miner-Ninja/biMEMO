import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MessageList } from "@/components/chat/MessageList";
import type { Message } from "@/types/chat";

function buildMessage(
  id: string,
  role: "user" | "assistant",
  content: string
): Message {
  return {
    id,
    role,
    content,
    timestamp: new Date("2026-03-28T10:00:00Z"),
  };
}

describe("MessageList", () => {
  it("scrolls back to the bottom when a new response is explicitly requested", () => {
    const scrollTo = vi.fn();
    const { container, rerender } = render(
      <MessageList
        conversationKey="chat-1"
        autoScrollRequest={0}
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
      />
    );

    const viewport = container.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]'
    );
    expect(viewport).not.toBeNull();

    Object.defineProperty(viewport!, "scrollHeight", {
      configurable: true,
      value: 1200,
    });
    Object.defineProperty(viewport!, "clientHeight", {
      configurable: true,
      value: 400,
    });
    Object.defineProperty(viewport!, "scrollTop", {
      configurable: true,
      writable: true,
      value: 0,
    });
    viewport!.scrollTo = scrollTo;

    viewport!.dispatchEvent(new Event("scroll"));

    rerender(
      <MessageList
        conversationKey="chat-1"
        autoScrollRequest={1}
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
          buildMessage("assistant-2", "assistant", "next response"),
        ]}
      />
    );

    expect(scrollTo).toHaveBeenCalledWith({
      top: 1200,
      behavior: "auto",
    });
  });
});
