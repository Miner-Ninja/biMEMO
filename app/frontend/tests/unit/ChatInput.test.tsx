import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInput } from "@/components/chat/ChatInput";

describe("ChatInput", () => {
  it("renders with default placeholder", () => {
    render(<ChatInput onSend={vi.fn()} />);
    expect(
      screen.getByPlaceholderText("Ask a question about your data...")
    ).toBeInTheDocument();
  });

  it("renders with custom placeholder", () => {
    render(<ChatInput onSend={vi.fn()} placeholder="Custom placeholder" />);
    expect(screen.getByPlaceholderText("Custom placeholder")).toBeInTheDocument();
  });

  it("shows Send button when not loading", () => {
    render(<ChatInput onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Send message" })).toBeInTheDocument();
  });

  it("shows Stop button when loading", () => {
    render(<ChatInput onSend={vi.fn()} isLoading={true} />);
    expect(screen.getByRole("button", { name: "Stop generation" })).toBeInTheDocument();
  });

  it("Send button is disabled when input is empty", () => {
    render(<ChatInput onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("Send button is enabled when input has text", async () => {
    const user = userEvent.setup();
    render(<ChatInput onSend={vi.fn()} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    await user.type(textarea, "Hello");

    expect(screen.getByRole("button", { name: "Send message" })).not.toBeDisabled();
  });

  it("calls onSend when Send button is clicked", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    await user.type(textarea, "Test message");

    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSend).toHaveBeenCalledWith("Test message", undefined, []);
  });

  it("calls onSend when Enter is pressed", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    await user.type(textarea, "Test message{Enter}");

    expect(onSend).toHaveBeenCalledWith("Test message", undefined, []);
  });

  it("does not call onSend on Shift+Enter (newline)", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    await user.type(textarea, "Line 1{Shift>}{Enter}{/Shift}Line 2");

    expect(onSend).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("Line 1\nLine 2");
  });

  it("clears input after sending", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    await user.type(textarea, "Test message");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(textarea).toHaveValue("");
  });

  it("calls onAbort when Stop button is clicked", async () => {
    const user = userEvent.setup();
    const onAbort = vi.fn();
    render(<ChatInput onSend={vi.fn()} onAbort={onAbort} isLoading={true} />);

    await user.click(screen.getByRole("button", { name: "Stop generation" }));

    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it("disables textarea when disabled prop is true", () => {
    render(<ChatInput onSend={vi.fn()} disabled={true} />);
    expect(
      screen.getByPlaceholderText("Ask a question about your data...")
    ).toBeDisabled();
  });

  it("does not call onSend when disabled", async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={true} />);

    // Can't type when disabled, so just check button is disabled
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("trims whitespace from message", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    await user.type(textarea, "  trimmed message  ");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSend).toHaveBeenCalledWith("trimmed message", undefined, []);
  });

  it("does not send empty message after trimming", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    await user.type(textarea, "   ");

    // Button should still be disabled because trimmed value is empty
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("applies cursor-wait style when loading", () => {
    const { container } = render(<ChatInput onSend={vi.fn()} isLoading={true} />);
    // The root div gets the cursor-wait class while loading
    const root = container.firstElementChild;
    expect(root).toHaveClass("[&_*]:cursor-wait");
  });

  it("Stop button has destructive variant", () => {
    render(<ChatInput onSend={vi.fn()} isLoading={true} />);
    const stopButton = screen.getByRole("button", { name: "Stop generation" });
    expect(stopButton).toHaveAttribute("data-variant", "destructive");
  });

  it("attaches pasted clipboard images to the message", () => {
    render(<ChatInput onSend={vi.fn()} />);

    const textarea = screen.getByPlaceholderText("Ask a question about your data...");
    const clipboardImage = new File(["image-bytes"], "clipboard-image.png", {
      type: "image/png",
    });

    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          {
            kind: "file",
            type: "image/png",
            getAsFile: () => clipboardImage,
          },
        ],
      },
    });

    expect(screen.getByText("clipboard-image.png")).toBeInTheDocument();
  });

  it("shows bridge providers menu and toggles a provider", async () => {
    const user = userEvent.setup();
    const onToggleBridgeProvider = vi.fn();

    render(
      <ChatInput
        onSend={vi.fn()}
        bridgeProviders={[{ provider: "discord", enabled: false }]}
        onToggleBridgeProvider={onToggleBridgeProvider}
      />
    );

    await user.click(screen.getByRole("button", { name: "Bridge providers" }));
    await user.click(screen.getByRole("switch", { name: "Discord" }));

    expect(onToggleBridgeProvider).toHaveBeenCalledWith("discord", true);
  });
});
