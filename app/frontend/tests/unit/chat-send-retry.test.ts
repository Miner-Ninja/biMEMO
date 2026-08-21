import { describe, expect, it } from "vitest";

import {
  resolveChatSendIds,
  type FailedChatSend,
} from "@/features/chat/model/chat-response-source";

// Retry-after-failure semantics (R6): the client remembers the identifiers
// of a send that failed on the wire, and resending the same text in the
// same conversation retries that exact pair — so the server dedups the row
// (client_message_id) and replays the turn (run_id) instead of creating a
// duplicate.

function failedSend(overrides: Partial<FailedChatSend> = {}): FailedChatSend {
  return {
    conversationId: "conversation-1",
    content: "hello",
    runId: "run-failed",
    clientMessageId: "run-failed",
    userMessageId: "local-user-1",
    assistantMessageId: "local-assistant-1",
    ...overrides,
  };
}

describe("chat send retry (R6)", () => {
  it("retries the identical id pair after a failure", () => {
    const failure = failedSend();
    const first = resolveChatSendIds(
      null,
      { conversationId: "conversation-1", content: "hello" },
      null,
      () => "run-failed"
    );
    expect(first).toMatchObject({
      runId: "run-failed",
      clientMessageId: "run-failed",
      source: "new",
    });

    const retry = resolveChatSendIds(
      failure,
      { conversationId: "conversation-1", content: "hello" },
      null,
      () => "must-not-be-used"
    );
    expect(retry.runId).toBe(first.runId);
    expect(retry.clientMessageId).toBe(first.clientMessageId);
    expect(retry.source).toBe("retry");
  });

  it("does not reuse ids once the failed send is superseded (record cleared)", () => {
    // After a successful send or a different send, the failure record is
    // consumed; the same text sent again is a NEW message with fresh ids.
    const retry = resolveChatSendIds(
      null,
      { conversationId: "conversation-1", content: "hello" },
      null,
      () => "run-after-success"
    );
    expect(retry.source).toBe("new");
    expect(retry.runId).toBe("run-after-success");
    expect(retry.clientMessageId).toBe("run-after-success");
  });

  it("does not reuse ids for a reworded message", () => {
    const result = resolveChatSendIds(
      failedSend(),
      { conversationId: "conversation-1", content: "hello, with more" },
      null,
      () => "run-fresh"
    );
    expect(result.source).toBe("new");
    expect(result.runId).not.toBe("run-failed");
    expect(result.clientMessageId).toBe(result.runId);
  });

  it("does not reuse ids across conversations", () => {
    const result = resolveChatSendIds(
      failedSend(),
      { conversationId: "conversation-2", content: "hello" },
      null,
      () => "run-fresh"
    );
    expect(result.source).toBe("new");
    expect(result.runId).not.toBe("run-failed");
  });

  it("gives each pending-input answer a fresh client_message_id on the same run", () => {
    // Two answers within one paused turn are two distinct message rows:
    // the same run_id, but a new client_message_id per answer.
    const pending = { run_id: "turn-7" };
    const first = resolveChatSendIds(
      null,
      { conversationId: "conversation-1", content: "1" },
      pending,
      () => "row-1"
    );
    const second = resolveChatSendIds(
      null,
      { conversationId: "conversation-1", content: "2" },
      pending,
      () => "row-2"
    );
    expect(first.runId).toBe("turn-7");
    expect(second.runId).toBe("turn-7");
    expect(first.clientMessageId).toBe("row-1");
    expect(second.clientMessageId).toBe("row-2");
    expect(first.clientMessageId).not.toBe(second.clientMessageId);
  });
});
