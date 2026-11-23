import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComposerStore } from "../chrome/composerStore";
import { NotificationToaster } from "../chrome/NotificationBanner";
import { Toaster, toast } from "./Toast";

beforeEach(() => toast.clear());
afterEach(() => { toast.clear(); vi.useRealTimers(); });

describe("Toast and Toaster", () => {
  it("shows at most three until +N more is expanded", () => {
    render(<Toaster />);
    act(() => {
      for (let i = 0; i < 5; i++) toast.show({ title: "Notice " + i, variant: "warning" });
    });
    expect(screen.getAllByRole("status")).toHaveLength(3);
    const more = screen.getByRole("button", { name: "+2 more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(more);
    expect(screen.getAllByRole("status")).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(screen.getAllByRole("status")).toHaveLength(3);
  });

  it("removes info and success at five seconds; warning and error persist", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => {
      toast.show({ title: "Saved", variant: "success" });
      toast.show({ title: "Connected", variant: "info" });
      toast.show({ title: "Check settings", variant: "warning" });
    });
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.queryByText("Saved")).toBeNull();
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.getByText("Check settings")).toBeVisible();
    act(() => toast.show({ title: "Failed", variant: "error" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Failed");
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByRole("alert")).toBeVisible();
  });

  it("pauses the dismissal clock during hover and resumes the remainder", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => toast.show({ title: "Uploaded", variant: "success" }));
    act(() => vi.advanceTimersByTime(2000));
    fireEvent.mouseEnter(screen.getByRole("status"));
    act(() => vi.advanceTimersByTime(10000));
    expect(screen.getByText("Uploaded")).toBeVisible();
    fireEvent.mouseLeave(screen.getByRole("status"));
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.queryByText("Uploaded")).toBeNull();
  });

  it("pauses while a toast control has keyboard focus", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => toast.show({ title: "Finished", variant: "info" }));
    act(() => vi.advanceTimersByTime(1200));
    screen.getByRole("button", { name: "Dismiss notification" }).focus();
    act(() => vi.advanceTimersByTime(10000));
    expect(screen.getByText("Finished")).toBeVisible();
    fireEvent.blur(screen.getByRole("button", { name: "Dismiss notification" }), { relatedTarget: document.body });
    act(() => vi.advanceTimersByTime(3800));
    expect(screen.queryByText("Finished")).toBeNull();
  });

  it("renders details, copy and an action and preserves the old dismiss selector", () => {
    vi.useFakeTimers();
    const click = vi.fn();
    render(<Toaster />);
    act(() => toast.show({ title: "Couldn't export", variant: "error",
      details: "Stack trace", action: { label: "Open log folder", onClick: click } }));
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByRole("region", { name: "Details" })).toHaveTextContent("Stack trace");
    expect(screen.getByRole("button", { name: "Copy details" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open log folder" }));
    expect(click).toHaveBeenCalledOnce();
    const dismiss = screen.getByRole("button", { name: "Dismiss notification" });
    expect(dismiss).toHaveClass("notification-dismiss");
    fireEvent.click(dismiss);
    act(() => vi.advanceTimersByTime(120));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("dismisses backend notifications through the unchanged store method", () => {
    vi.useFakeTimers();
    const dismissNotification = vi.fn();
    const notification = { visible: true, message: "Backend failed", msgType: "error" as const, revision: 4 };
    const store = {
      subscribe: () => () => {},
      getNotification: () => notification,
      dismissNotification,
    } as unknown as ComposerStore;
    render(<NotificationToaster store={store} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Backend failed");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    act(() => vi.advanceTimersByTime(120));
    expect(dismissNotification).toHaveBeenCalledOnce();
  });
});
