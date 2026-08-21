import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PendingConfirmationPanel } from "@/features/chat/components/PendingConfirmationPanel";
import type { PendingConfirmation } from "@/features/chat/model/chat";

function confirmation(
  overrides: Partial<PendingConfirmation> = {}
): PendingConfirmation {
  return {
    action_id: "a1",
    tool_name: "execute_command",
    parameters: { command: "ls -la" },
    status: "pending",
    grant_capable: false,
    url_host: null,
    ...overrides,
  };
}

describe("PendingConfirmationPanel", () => {
  it("renders two buttons for a per_invocation tool (S2.1)", () => {
    render(
      <PendingConfirmationPanel
        confirmation={confirmation()}
        onDecide={vi.fn()}
        busy={false}
      />
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    expect(screen.getByText("Allow once")).toBeInTheDocument();
    expect(screen.getByText("Deny")).toBeInTheDocument();
    expect(screen.queryByText(/conversation/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Allow for .*\./)).not.toBeInTheDocument();
  });

  it("renders four buttons for a grant-capable URL tool (S2.2, S2.4)", () => {
    render(
      <PendingConfirmationPanel
        confirmation={confirmation({
          tool_name: "http_fetch",
          parameters: { url: "https://example.com/prices" },
          grant_capable: true,
          url_host: "example.com",
        })}
        onDecide={vi.fn()}
        busy={false}
      />
    );
    expect(screen.getAllByRole("button")).toHaveLength(4);
    expect(screen.getByText("Allow for example.com")).toBeInTheDocument();
    expect(screen.getByText("Allow for this conversation")).toBeInTheDocument();
  });

  it("renders three buttons for a grant-capable non-URL tool", () => {
    render(
      <PendingConfirmationPanel
        confirmation={confirmation({
          tool_name: "web_search",
          parameters: { query: "x" },
          grant_capable: true,
          url_host: null,
        })}
        onDecide={vi.fn()}
        busy={false}
      />
    );
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("one press is one complete decision (S2.3, S3.1)", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn();
    render(
      <PendingConfirmationPanel
        confirmation={confirmation({
          tool_name: "http_fetch",
          parameters: { url: "https://example.com/a" },
          grant_capable: true,
          url_host: "example.com",
        })}
        onDecide={onDecide}
        busy={false}
      />
    );
    await user.click(screen.getByText("Allow once"));
    expect(onDecide).toHaveBeenCalledWith("approve", "once");
    await user.click(screen.getByText("Allow for example.com"));
    expect(onDecide).toHaveBeenCalledWith("approve", "domain");
    await user.click(screen.getByText("Deny"));
    expect(onDecide).toHaveBeenCalledWith("deny", "once");
  });

  it("disables every button while busy (R9)", () => {
    render(
      <PendingConfirmationPanel
        confirmation={confirmation({ grant_capable: true })}
        onDecide={vi.fn()}
        busy={true}
      />
    );
    for (const button of screen.getAllByRole("button")) {
      expect(button).toBeDisabled();
    }
  });

  it("renders the card inside the centered chat column frame", () => {
    const { container } = render(
      <PendingConfirmationPanel
        confirmation={confirmation()}
        onDecide={() => {}}
        busy={false}
      />
    );
    const frame = container.firstElementChild as HTMLElement;
    const card = container.querySelector(
      '[data-testid="pending-confirmation-panel"]'
    ) as HTMLElement;
    expect(frame.className).toContain("mx-auto");
    expect(frame.className).toContain("max-w-[1920px]");
    expect(card.className).toContain("max-w-3xl");
    expect(card.className).toContain("mx-auto");
  });

  it("shows the expired state instead of buttons when not actionable", () => {
    render(
      <PendingConfirmationPanel
        confirmation={confirmation({ status: "expired" })}
        onDecide={vi.fn()}
        busy={false}
        actionable={false}
      />
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/expired/i)).toBeInTheDocument();
  });
});
