/**
 * ADR-019: the node views load one chunk per kind. These tests cover the two
 * ways that can go wrong at runtime, neither of which the split shipped with.
 *
 * The failure mode that matters: React.lazy throws a PROMISE, which Suspense
 * catches - but when that promise REJECTS, Suspense has nothing to do with it
 * and the rejection propagates uncaught. React then unmounts the whole tree.
 * One node kind failing to load (a stale asset index after a deploy, a dropped
 * connection mid-session) would blank the entire canvas and the app chrome
 * with it. Measured before the boundary existed: the rejection surfaced as an
 * unhandled error.
 */

import { lazy, type ComponentType } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as lazyNodeViews from "./lazyNodeViews";

// The real module's own wrapper shape, applied to controllable chunks. Kept
// structurally identical to withNodeSuspense so a change there that drops the
// boundary fails here (see the export-shape test at the bottom, which pins
// that these really are the same wrapper).
function neverResolves() {
  return new Promise<{ default: ComponentType }>(() => {});
}

describe("a chunk that fails to load", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  // This is the regression proof. Before NodeChunkBoundary existed, a
  // rejecting chunk inside only a Suspense boundary surfaced as an UNHANDLED
  // error - Suspense catches the thrown promise, never its rejection - and
  // React unmounted the tree. Demonstrating that directly is not possible in
  // a test file, because the unhandled rejection fails the vitest run itself
  // (measured: exit 1). The surviving "App chrome" assertion below is the
  // same claim, stated positively.
  it("is contained to its own card - the surrounding tree survives", async () => {
    const Failing = lazyNodeViews.__testing.withNodeSuspense(
      lazy(() => Promise.reject(new Error("Failed to fetch dynamically imported module"))),
    );

    render(
      <div>
        <h1>App chrome</h1>
        <Failing />
      </div>,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    // The point of the whole exercise: everything around the bad card is
    // still mounted.
    expect(screen.getByText("App chrome")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("could not load");
  });

  it("says how to recover, and does not offer a retry that cannot work", async () => {
    const Failing = lazyNodeViews.__testing.withNodeSuspense(
      lazy(() => Promise.reject(new Error("boom"))),
    );
    render(<Failing />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    // React.lazy caches the rejection, so re-rendering replays the failure
    // forever. A retry button here would be a button that never works.
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Reload");
  });

  it("logs the underlying error rather than swallowing it", async () => {
    const Failing = lazyNodeViews.__testing.withNodeSuspense(
      lazy(() => Promise.reject(new Error("chunk 404"))),
    );
    render(<Failing />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    const logged = (console.error as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(logged.some((call) => String(call[0]).includes("chunk failed to load"))).toBe(true);
  });
});

describe("a chunk that is still loading", () => {
  it("shows the loading shell, which export polls for", () => {
    const Pending = lazyNodeViews.__testing.withNodeSuspense(lazy(neverResolves));
    const { container } = render(<Pending />);
    // exportCanvasPng.ts's NODE_LOADING_SELECTOR waits on exactly this class;
    // if it is ever renamed, the PNG export starts capturing loading shells.
    expect(container.querySelector(".scene-node-loading")).toBeTruthy();
    expect(container.querySelector('[aria-busy="true"]')).toBeTruthy();
  });
});

describe("the exported node views", () => {
  it("are all wrapped, so no kind can bypass the boundary", () => {
    const views = Object.entries(lazyNodeViews).filter(([name]) => name.endsWith("NodeView"));
    // 17 kinds; GroupNodeView backs both frame and container.
    expect(views.length).toBe(17);
    for (const [name, view] of views) {
      expect(typeof view, name).toBe("function");
      expect((view as ComponentType).displayName, name).toContain("LazyNodeView");
    }
  });
});
