import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AttachButton } from "@/components/chat/AttachButton";

describe("AttachButton", () => {
  it("rejects file types unsupported by the backend contract", () => {
    const onFileSelect = vi.fn();

    render(<AttachButton onFileSelect={onFileSelect} />);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["doc"], "report.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });

    fireEvent.change(input, { target: { files: [file] } });

    expect(onFileSelect).not.toHaveBeenCalled();
    expect(screen.getByText("This file type is not supported.")).toBeInTheDocument();
  });

  it("accepts markdown files supported by the backend contract", () => {
    const onFileSelect = vi.fn();

    render(<AttachButton onFileSelect={onFileSelect} />);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["# records"], "records.md", { type: "text/markdown" });

    fireEvent.change(input, { target: { files: [file] } });

    expect(onFileSelect).toHaveBeenCalledWith([file]);
    expect(
      screen.queryByText("This file type is not supported.")
    ).not.toBeInTheDocument();
  });
});
