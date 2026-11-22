import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";
import { ButtonGroup } from "./ButtonGroup";

describe("ButtonGroup", () => {
  it("provides a named group and preserves the child button names", () => {
    render(
      <ButtonGroup label="History actions">
        <Button>Undo</Button>
        <Button>Redo</Button>
      </ButtonGroup>,
    );
    const group = screen.getByRole("group", { name: "History actions" });
    expect(group).toHaveClass("gl-button-group");
    expect(within(group).getAllByRole("button")).toHaveLength(2);
    expect(within(group).getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("preserves native Tab and Enter keyboard behaviour", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<ButtonGroup label="Actions"><Button onClick={onClick}>Save changes</Button></ButtonGroup>);
    await user.tab();
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
