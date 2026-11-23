import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TextField } from "./TextField";

describe("TextField", () => {
  it.each(["text", "search", "url", "password", "email"] as const)("labels a %s input", (type) => {
    render(<TextField label="Address" type={type} placeholder="Enter value" />);
    expect((type === "password" ? screen.getByLabelText("Address") : screen.getByRole(type === "search" ? "searchbox" : "textbox", { name: "Address" }))).toHaveAttribute("type", type);
  });
  it("links error and description and supports the mono, size and prefix variants", () => {
    render(<TextField label="API key" description="Keep safe" error="Required"
      mono size="sm" prefixIcon="lock" suffix={{ type: "unit", text: "ms" }} />);
    const input = screen.getByRole("textbox", { name: "API key" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Keep safe Required");
    expect(input.closest(".gl-text-field")).toHaveClass("gl-text-field-sm", "gl-text-field-mono");
    expect(screen.getByText("ms")).toBeInTheDocument();
  });
  it("edits and clears an uncontrolled value through a named button", async () => {
    const user = userEvent.setup(), onValueChange = vi.fn();
    render(<TextField label="Search query" defaultValue="graph" suffix={{ type: "clear" }} onValueChange={onValueChange} />);
    const input = screen.getByRole("textbox", { name: "Search query" });
    await user.type(input, "link");
    expect(input).toHaveValue("graphlink");
    await user.click(screen.getByRole("button", { name: "Clear Search query" }));
    expect(input).toHaveValue("");
    expect(onValueChange).toHaveBeenLastCalledWith("");
  });
  it("does not override a controlled value when clear is requested", async () => {
    const user = userEvent.setup(), onValueChange = vi.fn();
    const view = render(<TextField label="Project" value="Graphlink" onValueChange={onValueChange} suffix={{ type: "clear" }} />);
    await user.click(screen.getByRole("button", { name: "Clear Project" }));
    expect(onValueChange).toHaveBeenCalledWith("");
    expect(screen.getByRole("textbox", { name: "Project" })).toHaveValue("Graphlink");
    view.rerender(<TextField label="Project" value="" onValueChange={onValueChange} suffix={{ type: "clear" }} />);
    expect(screen.getByRole("textbox", { name: "Project" })).toHaveValue("");
  });
  it("reveals and hides passwords with an accessible toggle", async () => {
    const user = userEvent.setup();
    render(<TextField label="Password" type="password" defaultValue="secret" suffix={{ type: "password" }} />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show Password" }));
    expect(input).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide Password" }));
    expect(input).toHaveAttribute("type", "password");
  });
  it("disables both the native input and suffix button", () => {
    render(<TextField label="Fixed" disabled defaultValue="locked" suffix={{ type: "clear" }} />);
    expect(screen.getByRole("textbox", { name: "Fixed" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear Fixed" })).toBeDisabled();
  });
});
