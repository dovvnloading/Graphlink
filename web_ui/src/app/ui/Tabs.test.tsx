import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tabs } from "./Tabs";

const items = [
  { id: "details", label: "Details", content: <p>Details panel</p> },
  { id: "hidden", label: "Unavailable", content: <p>Unavailable panel</p>, disabled: true },
  { id: "activity", label: "Activity", content: <p>Activity panel</p> },
];

describe("Tabs", () => {
  it("renders named tabs and lazily mounts only the active panel", () => {
    render(<Tabs label="Inspector" items={items} />);
    expect(screen.getByRole("tablist", { name: "Inspector" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Details" })).toHaveTextContent("Details panel");
    expect(screen.queryByText("Activity panel")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Unavailable" })).toBeDisabled();
  });
  it("supports arrow navigation, wrap, Home/End and roving tabIndex", async () => {
    const user = userEvent.setup();
    render(<Tabs label="Panels" items={items} />);
    const first = screen.getByRole("tab", { name: "Details" });
    const last = screen.getByRole("tab", { name: "Activity" });
    first.focus();
    await user.keyboard("{ArrowRight}");
    expect(last).toHaveFocus();
    expect(last).toHaveAttribute("aria-selected", "true");
    expect(last).toHaveAttribute("tabindex", "0");
    expect(first).toHaveAttribute("tabindex", "-1");
    await user.keyboard("{ArrowRight}");
    expect(first).toHaveFocus();
    await user.keyboard("{End}");
    expect(last).toHaveFocus();
    await user.keyboard("{Home}");
    expect(first).toHaveFocus();
  });
  it("supports controlled selection and click callbacks", async () => {
    const user = userEvent.setup(), onValueChange = vi.fn();
    const view = render(<Tabs label="Panels" items={items} value="activity" onValueChange={onValueChange} />);
    await user.click(screen.getByRole("tab", { name: "Details" }));
    expect(onValueChange).toHaveBeenCalledWith("details");
    expect(screen.getByRole("tabpanel", { name: "Activity" })).toBeInTheDocument();
    view.rerender(<Tabs label="Panels" items={items} value="details" onValueChange={onValueChange} />);
    expect(screen.getByRole("tabpanel", { name: "Details" })).toBeInTheDocument();
  });
  it("honours defaultValue", () => {
    render(<Tabs label="Default" items={items} defaultValue="activity" />);
    expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
  });
});
