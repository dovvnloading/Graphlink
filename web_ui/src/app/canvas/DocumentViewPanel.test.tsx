import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DocumentViewPanel } from "./DocumentViewPanel";

describe("DocumentViewPanel", () => {
  it("renders the fixed title and the passed markdown content", () => {
    render(<DocumentViewPanel content={"# Heading\n\nA paragraph of body text."} onClose={vi.fn()} />);

    expect(screen.getByText("Document View")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
    expect(screen.getByText("A paragraph of body text.")).toBeInTheDocument();
  });

  it("does not crash and renders an empty body when content is null", () => {
    render(<DocumentViewPanel content={null} onClose={vi.fn()} />);

    expect(screen.getByText("Document View")).toBeInTheDocument();
  });

  it("clicking Close calls onClose - the only way this panel closes, unlike a Dialog", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DocumentViewPanel content="some content" onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders as a plain landmark region, not a modal dialog (no role='dialog', no scrim)", () => {
    render(<DocumentViewPanel content="some content" onClose={vi.fn()} />);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByLabelText("Document View")).toBeInTheDocument();
  });
});
