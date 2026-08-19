import { describe, expect, it } from "vitest";

import {
  bindPendingInputReply,
  bindSendIds,
  newChatSendIds,
  resolveChatSendIds,
  type FailedChatSend,
} from "@/lib/chat-response-source";

describe("bindPendingInputReply", () => {
  it("resumes the exact run and CAS version with a fresh message id", () => {
    expect(
      bindPendingInputReply(
        { message: "Berlin", conversation_id: "conversation-1" },
        {
          run_id: "turn-1",
          pending_input_id: "input-1",
          cas_version: 3,
        },
        "Berlin",
        "answer-row-1"
      )
    ).toEqual({
      message: "Berlin",
      conversation_id: "conversation-1",
      run_id: "turn-1",
      pending_input_id: "input-1",
      pending_input_version: 3,
      pending_input_value: "Berlin",
      client_message_id: "answer-row-1",
    });
  });
});

describe("newChatSendIds", () => {
  it("uses ONE pre-generated UUID for both run_id and client_message_id", () => {
    let calls = 0;
    const ids = newChatSendIds(() => {
      calls += 1;
      return `uuid-${calls}`;
    });
    expect(calls).toBe(1);
    expect(ids).toEqual({ runId: "uuid-1", clientMessageId: "uuid-1" });
  });
});

describe("bindSendIds", () => {
  it("binds a regular send to its run_id and client_message_id", () => {
    expect(
      bindSendIds(
        { message: "hello", conversation_id: "conversation-1" },
        { runId: "turn-9", clientMessageId: "turn-9" }
      )
    ).toEqual({
      message: "hello",
      conversation_id: "conversation-1",
      run_id: "turn-9",
      client_message_id: "turn-9",
    });
  });
});

describe("resolveChatSendIds", () => {
  const failedSend: FailedChatSend = {
    conversationId: "conversation-1",
    content: "hello",
    runId: "run-1",
    clientMessageId: "run-1",
    userMessageId: "user-1",
    assistantMessageId: "assistant-1",
  };

  it("takes fresh paired ids for a new message", () => {
    let n = 0;
    const result = resolveChatSendIds(
      null,
      { conversationId: "conversation-1", content: "brand new" },
      null,
      () => {
        n += 1;
        return `id-${n}`;
      }
    );
    expect(n).toBe(1);
    expect(result.source).toBe("new");
    expect(result.runId).toBe("id-1");
    expect(result.clientMessageId).toBe("id-1");
  });

  it("retries the failed send's ids for the same text in the same conversation", () => {
    const result = resolveChatSendIds(
      failedSend,
      { conversationId: "conversation-1", content: "hello" },
      null,
      () => "must-not-be-used"
    );
    expect(result.source).toBe("retry");
    expect(result.runId).toBe("run-1");
    expect(result.clientMessageId).toBe("run-1");
  });

  it("takes fresh ids when the text was reworded", () => {
    const result = resolveChatSendIds(
      failedSend,
      { conversationId: "conversation-1", content: "hello (edited)" },
      null,
      () => "fresh-1"
    );
    expect(result.source).toBe("new");
    expect(result.runId).toBe("fresh-1");
    expect(result.clientMessageId).toBe("fresh-1");
  });

  it("takes fresh ids in a different conversation", () => {
    const result = resolveChatSendIds(
      failedSend,
      { conversationId: "conversation-2", content: "hello" },
      null,
      () => "fresh-2"
    );
    expect(result.source).toBe("new");
    expect(result.runId).toBe("fresh-2");
  });

  it("keeps the turn's run_id with a fresh client_message_id for a pending-input answer", () => {
    let n = 0;
    const result = resolveChatSendIds(
      null,
      { conversationId: "conversation-1", content: "Berlin" },
      { run_id: "turn-7" },
      () => {
        n += 1;
        return `answer-${n}`;
      }
    );
    expect(n).toBe(1);
    expect(result.source).toBe("pending-input");
    expect(result.runId).toBe("turn-7");
    expect(result.clientMessageId).toBe("answer-1");
    expect(result.clientMessageId).not.toBe(result.runId);
  });

  it("takes the pending-input answer over a retry match", () => {
    const result = resolveChatSendIds(
      { ...failedSend, content: "Berlin" },
      { conversationId: "conversation-1", content: "Berlin" },
      { run_id: "turn-7" },
      () => "answer-9"
    );
    expect(result.source).toBe("pending-input");
    expect(result.runId).toBe("turn-7");
    expect(result.clientMessageId).toBe("answer-9");
  });
});
