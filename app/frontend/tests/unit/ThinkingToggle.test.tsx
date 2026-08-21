/**
 * Tests for the single thinking on/off toggle (chat-thinking-toggle):
 * R3/S1.2 — with the toggle off, thinking blocks are hidden even for
 * messages that carry saved thinking; S1.4 — on shows them.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ChatMessage } from "@/features/chat/components/ChatMessage";
import type { Message } from "@/features/chat/model/chat";

const MESSAGE: Message = {
  id: "m1",
  role: "assistant",
  content: "The answer.",
  timestamp: new Date("2026-08-12T10:00:00"),
  thinking: "step one, step two",
};

describe("thinking toggle display gate", () => {
  it("S1.2 — thinking block hidden when the toggle is off", () => {
    render(<ChatMessage message={MESSAGE} showThinking={false} />);
    expect(screen.queryByText(/step one, step two/)).toBeNull();
    expect(screen.getByText(/The answer/)).toBeTruthy();
  });

  it("S1.4 — thinking block visible when the toggle is on", () => {
    render(<ChatMessage message={MESSAGE} showThinking />);
    expect(screen.getByText(/step one, step two/)).toBeTruthy();
  });
});
