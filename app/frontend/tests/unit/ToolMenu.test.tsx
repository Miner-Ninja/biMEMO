import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToolMenu } from "@/components/chat/ToolMenu";
import { listToolCatalog } from "@/api/scheduled-tasks";

vi.mock("@/api/scheduled-tasks", () => ({
  listToolCatalog: vi.fn(),
}));

const mockedListToolCatalog = vi.mocked(listToolCatalog);

describe("ToolMenu", () => {
  beforeEach(() => {
    mockedListToolCatalog.mockReset();
    mockedListToolCatalog.mockResolvedValue([
      { name: "web_search", description: "Search the web" },
      { name: "custom_tool", description: "Runtime-provided tool" },
    ]);
  });

  it("loads the runtime catalog only when opened and toggles a tool", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<ToolMenu selected={[]} onToggle={onToggle} />);

    expect(mockedListToolCatalog).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Tools" }));

    await waitFor(() => expect(mockedListToolCatalog).toHaveBeenCalledTimes(1));
    await user.click(await screen.findByRole("menuitem", { name: "web_search" }));

    expect(onToggle).toHaveBeenCalledWith("web_search");
    expect(screen.getByRole("menuitem", { name: "custom_tool" })).toBeInTheDocument();
  });
});
