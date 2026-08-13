import { describe, expect, it } from "vitest";

import { bindPendingInputReply } from "../src/lib/chat-response-source";

describe("bindPendingInputReply", () => {
  it("resumes the exact run and CAS version", () => {
    expect(
      bindPendingInputReply(
        { message: "Berlin", conversation_id: "conversation-1" },
        {
          run_id: "turn-1",
          pending_input_id: "input-1",
          cas_version: 3,
        },
        "Berlin"
      )
    ).toEqual({
      message: "Berlin",
      conversation_id: "conversation-1",
      run_id: "turn-1",
      pending_input_id: "input-1",
      pending_input_version: 3,
      pending_input_value: "Berlin",
    });
  });
});
