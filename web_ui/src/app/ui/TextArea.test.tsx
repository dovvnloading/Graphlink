import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TextArea } from "./TextArea";

describe("TextArea", () => {
  it("provides a labeled native textbox with description and errors", () => {
    render(<TextArea label="Notes" description="Plain text" error="Too long" />);
    const input = screen.getByRole("textbox", { name: "Notes" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Plain text Too long");
    expect(input).toHaveAttribute("rows", "2");
  });
  it("supports uncontrolled edits and controlled values", async () => {
    const user = userEvent.setup(), onValueChange = vi.fn();
    const view = render(<TextArea label="Comment" defaultValue="Hello" onValueChange={onValueChange} />);
    const input = screen.getByRole("textbox", { name: "Comment" });
    await user.type(input, "!");
    expect(input).toHaveValue("Hello!");
    expect(onValueChange).toHaveBeenLastCalledWith("Hello!");
    view.rerender(<TextArea label="Comment" value="Controlled" onValueChange={onValueChange} />);
    expect(input).toHaveValue("Controlled");
    await user.type(input, "!");
    expect(input).toHaveValue("Controlled");
  });
  it("clamps its measured height between row limits and scrolls when needed", () => {
    render(<TextArea label="Summary" minRows={2} maxRows={4} />);
    const input = screen.getByRole("textbox", { name: "Summary" }) as HTMLTextAreaElement;
    Object.defineProperty(input, "scrollHeight", { configurable: true, value: 500 });
    fireEvent.change(input, { target: { value: "Long paragraph" } });
    expect(parseFloat(input.style.height)).toBeGreaterThan(0);
    expect(input.style.overflowY).toBe("auto");
  });
  it("disables input and respects the minimum row count", () => {
    render(<TextArea label="Archived notes" minRows={3} maxRows={2} disabled />);
    expect(screen.getByRole("textbox", { name: "Archived notes" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Archived notes" })).toHaveAttribute("rows", "3");
  });
});
