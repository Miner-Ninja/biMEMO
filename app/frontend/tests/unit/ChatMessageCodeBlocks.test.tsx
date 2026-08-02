import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { ChatMessage } from "../../src/components/chat/ChatMessage";
import { ThemeProvider } from "../../src/components/layout/ThemeProvider";
import type { Message } from "../../src/types/chat";

beforeAll(() => {
  Object.defineProperty(window, "localStorage", {
    writable: true,
    value: {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
    },
  });

  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === "(prefers-color-scheme: dark)",
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

function renderMessage(message: Message) {
  return render(
    <ThemeProvider>
      <ChatMessage message={message} />
    </ThemeProvider>
  );
}

describe("ChatMessage code blocks", () => {
  it("renders highlighted code blocks without command click handlers", () => {
    renderMessage({
      id: "assistant-message-1",
      role: "assistant",
      content: '```python\nprint(\"hello\")\n```',
      timestamp: new Date("2026-03-13T16:00:00Z"),
    });

    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  });

  it("renders highlighted indented code blocks without explicit fences", () => {
    renderMessage({
      id: "assistant-message-2",
      role: "assistant",
      content:
        "Пример:\n\n    def count_words(text):\n        return len(text.split())",
      timestamp: new Date("2026-03-13T16:10:00Z"),
    });

    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  });
});
