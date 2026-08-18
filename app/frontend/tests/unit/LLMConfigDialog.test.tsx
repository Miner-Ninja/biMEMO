/**
 * Tests for the tabbed LLM config dialog (llm-config-tabs):
 * embedding fields keep their save/pre-fill behaviour (S1.2, S1.3)
 * and list-load failures are visible and clearable (S1.1, S1.4).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/llm", () => ({
  testLLMConnection: vi.fn(),
  getModelList: vi.fn().mockResolvedValue({ models: [] }),
}));

import { getModelList, testLLMConnection } from "@/api/llm";
import { LLMConfigDialog } from "@/components/LLMConfigDialog";

const openVectorsTab = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole("button", { name: /Vector Memory/ }));
};

describe("LLMConfigDialog embedding fields", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getModelList).mockResolvedValue({ models: [] });
  });

  it("S1.2 — entering an embedding model sends it on save", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<LLMConfigDialog open onClose={vi.fn()} onSave={onSave} />);

    await openVectorsTab(user);
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

  it("S1.3 — an existing embedding model pre-fills the field", async () => {
    const user = userEvent.setup();
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
    await openVectorsTab(user);
    expect(
      (await screen.findByDisplayValue("nomic-embed-text")) as HTMLInputElement
    ).toBeTruthy();
  });
});

describe("LLMConfigDialog load-failure indication", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("S1.1 — a failed load shows a red message and a tab marker", async () => {
    vi.mocked(getModelList).mockRejectedValue(new Error("400 Bad Request"));
    const user = userEvent.setup();
    render(<LLMConfigDialog open onClose={vi.fn()} onSave={vi.fn()} />);

    await openVectorsTab(user);
    await user.click(screen.getByRole("button", { name: "Load Models" }));

    expect(await screen.findByText(/Could not load the model list/)).toBeTruthy();
    expect(screen.getByText(/400 Bad Request/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Vector Memory/ }).textContent).toContain(
      "•"
    );
  });

  it("S1.4 — editing the address clears the error message", async () => {
    vi.mocked(getModelList).mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<LLMConfigDialog open onClose={vi.fn()} onSave={vi.fn()} />);

    await openVectorsTab(user);
    await user.click(screen.getByRole("button", { name: "Load Models" }));
    expect(await screen.findByText(/Could not load the model list/)).toBeTruthy();

    await user.type(screen.getByPlaceholderText("http://vllm-embeddings:8000"), "x");
    expect(screen.queryByText(/Could not load the model list/)).toBeNull();
  });
});

describe("LLMConfigDialog per-tab Test Connection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getModelList).mockResolvedValue({ models: [] });
  });

  it("S2.1 — on the vectors tab the button probes the embedding endpoint", async () => {
    vi.mocked(testLLMConnection).mockResolvedValue({
      ok: true,
      provider: "custom",
      dimension: 1024,
    });
    const user = userEvent.setup();
    render(<LLMConfigDialog open onClose={vi.fn()} onSave={vi.fn()} />);

    await openVectorsTab(user);
    await user.type(
      screen.getByPlaceholderText("Leave blank to disable vector search"),
      "qwen3-embedding:0.6b"
    );
    await user.click(screen.getByRole("button", { name: "Test Connection" }));

    expect(testLLMConnection).toHaveBeenCalledTimes(1);
    expect(vi.mocked(testLLMConnection).mock.calls[0][0]).toMatchObject({
      probe: "embedding",
      model: "qwen3-embedding:0.6b",
    });
    expect(await screen.findByText(/dimension 1024/)).toBeTruthy();
  });

  it("S2.2 — an empty embedding model blocks the probe with a hint", async () => {
    const user = userEvent.setup();
    render(<LLMConfigDialog open onClose={vi.fn()} onSave={vi.fn()} />);

    await openVectorsTab(user);
    await user.click(screen.getByRole("button", { name: "Test Connection" }));

    expect(testLLMConnection).not.toHaveBeenCalled();
    expect(await screen.findByText(/Enter an embedding model/)).toBeTruthy();
  });
});
