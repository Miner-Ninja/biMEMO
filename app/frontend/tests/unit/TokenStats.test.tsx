import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TokenStats } from "@/features/chat/components/TokenStats";

describe("TokenStats", () => {
  it("renders token, timing, and context details when context usage exists", () => {
    render(
      <TokenStats
        stats={{
          prompt_tokens: 321,
          completion_tokens: 54,
          total_tokens: 375,
          context: {
            total: 32768,
            used: 4096,
            free: 28672,
            used_percent: 12.5,
          },
          timing: {
            prompt_ms: 450,
            generation_ms: 1600,
            total_ms: 2200,
          },
          model: "qwen3.5:35b",
          iterations: 2,
        }}
      />
    );

    expect(screen.getByText("321 in / 54 out")).toBeInTheDocument();
    expect(screen.getByText("2.2s")).toBeInTheDocument();
    expect(screen.getByText("33.8 tok/s")).toBeInTheDocument();
    expect(screen.getByText("4.1K / 32.8K ctx (13%)")).toBeInTheDocument();
    expect(screen.getByText("(2 iter)")).toBeInTheDocument();
  });

  it("hides the context segment when provider does not expose context usage", () => {
    render(
      <TokenStats
        stats={{
          prompt_tokens: 100,
          completion_tokens: 25,
          total_tokens: 125,
          timing: {
            prompt_ms: 200,
            generation_ms: 800,
            total_ms: 1000,
          },
          model: "qwen3:8b",
          iterations: 1,
        }}
      />
    );

    expect(screen.getByText("100 in / 25 out")).toBeInTheDocument();
    expect(screen.getByText("1.0s")).toBeInTheDocument();
    expect(screen.getByText("31.3 tok/s")).toBeInTheDocument();
    expect(screen.queryByText(/ctx \(/)).not.toBeInTheDocument();
  });

  it("shows the per-phase breakdown in the time tooltip when stages exist", () => {
    render(
      <TokenStats
        stats={{
          prompt_tokens: 100,
          completion_tokens: 25,
          total_tokens: 125,
          timing: {
            prompt_ms: 120,
            generation_ms: 9900,
            total_ms: 11440,
            stages: [
              { label: "Memory recall", duration_ms: 340 },
              { label: "Web search", duration_ms: 1200 },
              { label: "Prompt", duration_ms: 120 },
              { label: "Generation", duration_ms: 9900 },
            ],
          },
          model: "qwen3:8b",
          iterations: 1,
        }}
      />
    );

    // Query via the (unique) total-time text, then read the raw title attribute
    // to avoid Testing Library's whitespace/newline normalisation in getByTitle.
    const timeBadge = screen.getByText("11.4s").closest("[title]");
    expect(timeBadge).toHaveAttribute(
      "title",
      "Memory recall  340ms\nWeb search  1.2s\nPrompt  120ms\nGeneration  9.9s"
    );
  });

  it("falls back to the Prompt/Generation tooltip when no stages exist", () => {
    render(
      <TokenStats
        stats={{
          prompt_tokens: 100,
          completion_tokens: 25,
          total_tokens: 125,
          timing: {
            prompt_ms: 200,
            generation_ms: 800,
            total_ms: 1000,
          },
          model: "qwen3:8b",
          iterations: 1,
        }}
      />
    );

    expect(screen.getByTitle("Prompt: 200ms, Generation: 800ms")).toBeInTheDocument();
  });
});
