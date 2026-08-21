import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MessageList } from "@/features/chat/components/MessageList";
import type { Message } from "@/features/chat/model/chat";

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

function setViewportGeometry(
  viewport: HTMLElement,
  scrollHeight: number,
  clientHeight: number,
  scrollTop: number
) {
  Object.defineProperty(viewport, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
  Object.defineProperty(viewport, "clientHeight", {
    configurable: true,
    value: clientHeight,
  });
  Object.defineProperty(viewport, "scrollTop", {
    configurable: true,
    writable: true,
    value: scrollTop,
  });
  viewport.scrollTo = vi.fn();
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

  it("fires onReachTop when the viewport is scrolled near the top of the history", () => {
    const onReachTop = vi.fn();
    const { container } = render(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        onReachTop={onReachTop}
      />
    );

    const viewport = container.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]'
    )!;
    setViewportGeometry(viewport, 1200, 400, 100);

    // 100px from the top: not reached.
    viewport.dispatchEvent(new Event("scroll"));
    expect(onReachTop).not.toHaveBeenCalled();

    // 79px from the top: reached (threshold is < 80).
    viewport.scrollTop = 79;
    viewport.dispatchEvent(new Event("scroll"));
    expect(onReachTop).toHaveBeenCalledTimes(1);

    viewport.scrollTop = 0;
    viewport.dispatchEvent(new Event("scroll"));
    expect(onReachTop).toHaveBeenCalledTimes(2);
  });

  it("compensates the scroll position when an older page is prepended", () => {
    const { container, rerender } = render(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={null}
      />
    );

    const viewport = container.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]'
    )!;
    setViewportGeometry(viewport, 1200, 400, 30);
    // Re-render the same content so the layout pass measures the viewport
    // height while the user sits near the top.
    rerender(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={null}
      />
    );

    // The older page (500px of content) is prepended above the window.
    setViewportGeometry(viewport, 1700, 400, 30);
    rerender(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-0", "user", "older"),
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={{ conversationId: "chat-1", seq: 1 }}
      />
    );

    // The viewport moved down by exactly the prepended block's height:
    // the same message is still under the user's eyes — no jump.
    expect(viewport.scrollTop).toBe(30 + (1700 - 1200));
  });

  it("does not compensate when the prepend signal belongs to another conversation", () => {
    const { container, rerender } = render(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={null}
      />
    );

    const viewport = container.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]'
    )!;
    setViewportGeometry(viewport, 1200, 400, 30);
    rerender(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={null}
      />
    );

    setViewportGeometry(viewport, 1700, 400, 30);
    rerender(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-0", "user", "older"),
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={{ conversationId: "chat-2", seq: 1 }}
      />
    );

    // A foreign signal must not move this conversation's viewport.
    expect(viewport.scrollTop).toBe(30);
  });

  it("does not re-apply an already consumed prepend signal", () => {
    const { container, rerender } = render(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={null}
      />
    );

    const viewport = container.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]'
    )!;
    setViewportGeometry(viewport, 1200, 400, 30);
    rerender(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={null}
      />
    );

    setViewportGeometry(viewport, 1700, 400, 30);
    rerender(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-0", "user", "older"),
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={{ conversationId: "chat-1", seq: 1 }}
      />
    );
    expect(viewport.scrollTop).toBe(530);

    // The same signal arrives again (e.g. a re-render) without new content:
    // the compensation must not run a second time.
    viewport.scrollTop = 545;
    rerender(
      <MessageList
        conversationKey="chat-1"
        messages={[
          buildMessage("user-0", "user", "older"),
          buildMessage("user-1", "user", "hello"),
          buildMessage("assistant-1", "assistant", "world"),
        ]}
        prependSignal={{ conversationId: "chat-1", seq: 1 }}
      />
    );
    expect(viewport.scrollTop).toBe(545);
  });
});
