import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ChatMessage } from "../../src/components/chat/ChatMessage";
import type { Message } from "../../src/types/chat";

const IMAGE_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WnR6R4AAAAASUVORK5CYII=";

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "message-1",
    role: "user",
    content: "Describe this image",
    images: [IMAGE_DATA_URL],
    timestamp: new Date("2026-03-13T10:00:00Z"),
    ...overrides,
  };
}

describe("ChatMessage image attachments", () => {
  it("renders image thumbnails for persisted message images", () => {
    render(<ChatMessage message={makeMessage()} />);

    const thumbnail = screen.getByAltText("Attached image 1");
    expect(thumbnail).toBeInTheDocument();
    expect(thumbnail).toHaveAttribute("src", IMAGE_DATA_URL);
  });

  it("opens a full-size preview when the thumbnail is clicked", () => {
    render(<ChatMessage message={makeMessage()} />);

    fireEvent.click(screen.getByRole("button", { name: "Open image 1" }));

    expect(screen.getByRole("dialog", { name: "Image preview" })).toBeInTheDocument();
    expect(screen.getByAltText("Selected attachment preview")).toHaveAttribute(
      "src",
      IMAGE_DATA_URL
    );
  });
});
