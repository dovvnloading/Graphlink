import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";
import { Card } from "./Card";

describe("Card", () => {
  it("has an optional named header, status and actions", () => {
    const click = vi.fn();
    const { rerender } = render(
      <Card title="Build" status="Running" actions={<Button onClick={click}>Open log</Button>}>
        In progress
      </Card>,
    );
    const card = screen.getByRole("region", { name: "Build" });
    expect(card).toHaveTextContent("In progress");
    expect(screen.getByRole("heading", { name: "Build" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Running" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open log" }));
    expect(click).toHaveBeenCalledOnce();
    rerender(<Card>Simple content</Card>);
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByText("Simple content")).toBeVisible();
  });
});
