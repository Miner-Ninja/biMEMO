/**
 * Tests for the embedding fields in the LLM config dialog
 * (llm-embedding-field, S1.1 + S1.2).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/llm", () => ({
  testLLMConnection: vi.fn(),
  getModelList: vi.fn().mockResolvedValue({ models: [] }),
}));

import { LLMConfigDialog } from "@/components/LLMConfigDialog";

describe("LLMConfigDialog embedding fields", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("S1.1 — entering an embedding model sends it on save", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<LLMConfigDialog open onClose={vi.fn()} onSave={onSave} />);

    const embeddingInput = screen.getByPlaceholderText(
      "Leave blank to disable vector search"
    );
    await user.type(embeddingInput, "nomic-embed-text");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toMatchObject({
      embedding_model: "nomic-embed-text",
    });
  });

  it("S1.2 — an existing embedding model pre-fills the field", () => {
    render(
      <LLMConfigDialog
        open
        onClose={vi.fn()}
        onSave={vi.fn()}
        editConfig={{
          id: "c1",
          service_type: "ollama",
          model: "qwen3.5:9b",
          embedding_model: "nomic-embed-text",
          enabled: true,
          has_api_key: false,
          created_at: "2026-08-09T10:00:00",
        }}
      />
    );
    expect(
      screen.getByDisplayValue("nomic-embed-text") as HTMLInputElement
    ).toBeTruthy();
  });
});
