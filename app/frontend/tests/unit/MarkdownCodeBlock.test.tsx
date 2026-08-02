import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../src/components/layout/ThemeProvider";
import { MarkdownCodeBlock } from "../../src/components/chat/MarkdownCodeBlock";

beforeAll(() => {
  Object.defineProperty(window, "localStorage", {
    writable: true,
    value: {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
    },
  });

  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === "(prefers-color-scheme: dark)",
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

function renderWithTheme(code: string, language?: string) {
  return render(
    <ThemeProvider>
      <MarkdownCodeBlock code={code} language={language} />
    </ThemeProvider>
  );
}

describe("MarkdownCodeBlock", () => {
  it("renders the copy button for fenced code blocks", () => {
    renderWithTheme("Get-TimeZone", "powershell");

    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  });

  it("renders highlighted code output for explicit languages", async () => {
    renderWithTheme("tzutil /g", "powershell");

    await waitFor(() => {
      expect(document.querySelector(".hljs")).not.toBeNull();
    });
  });

  it("infers python highlighting when a fenced block omits the language", async () => {
    const { container } = renderWithTheme('def hello():\n    print("hi")');

    await waitFor(() => {
      expect(container.querySelector(".hljs")).not.toBeNull();
    });
  });
});
