import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ViewPopover } from "./ViewPopover";
import {
  initialDragSpeedState,
  initialFontControlState,
  initialGridState,
  initialSceneState,
} from "../canvas/sceneStore";
import { OverlayProvider, useOverlays } from "../overlays/overlays";

// ViewPopover renders via <Popover name="view">, which only mounts content
// while open - force it open through the real overlay context (same pattern
// PinOverlay.test.tsx already established), not a bypass.
function OpenViewOnMount({ children }: { children: React.ReactNode }) {
  const overlays = useOverlays();
  if (!overlays.isOpen("view")) overlays.open("view", "popover");
  return <>{children}</>;
}

function makeStore(
  sceneOverrides: Partial<typeof initialSceneState> = {},
  focusAcceptedPaths = false,
) {
  const listeners = new Set<() => void>();
  const scene = { ...initialSceneState, ...sceneOverrides };
  const setFadeConnections = vi.fn();
  const setSnapToGrid = vi.fn();
  const setOrthogonalConnections = vi.fn();
  const setSmartGuides = vi.fn();
  const setFocusAcceptedPaths = vi.fn();
  const store = {
    subscribe: (l: () => void) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    getScene: () => scene,
    getGrid: () => initialGridState,
    getDragConfig: () => initialDragSpeedState,
    getFontConfig: () => initialFontControlState,
    // ADR-002 Workstream 1 ("Branch status and lifecycle"): NOT part of
    // `scene` - see sceneStore.ts's own comment on why this field is
    // local UI state rather than backend-synced.
    getFocusAcceptedPaths: () => focusAcceptedPaths,
    setDragFactor: vi.fn(),
    setGridSize: vi.fn(),
    setGridOpacityPercent: vi.fn(),
    setGridStyle: vi.fn(),
    setGridColor: vi.fn(),
    setSnapToGrid,
    setFadeConnections,
    setOrthogonalConnections,
    setSmartGuides,
    setFocusAcceptedPaths,
    setFontFamily: vi.fn(),
    setFontSize: vi.fn(),
    setFontColor: vi.fn(),
  };
  return { store, setFadeConnections, setSnapToGrid, setOrthogonalConnections, setSmartGuides, setFocusAcceptedPaths };
}

function renderOpen(store: unknown) {
  return render(
    <OverlayProvider>
      <OpenViewOnMount>
        {/* @ts-expect-error - test double */}
        <ViewPopover store={store} />
      </OpenViewOnMount>
    </OverlayProvider>,
  );
}

// R7.5b-1: only the new Fade Connections checkbox is covered here -
// ViewPopover.tsx otherwise has no prior test coverage (a pre-existing gap,
// not introduced by this increment) and backfilling the rest is out of scope
// for a canvas-visuals increment.
describe("ViewPopover (R7.5b-1 Fade Connections checkbox)", () => {
  it("reflects fadeConnectionsEnabled=false as unchecked", () => {
    const { store } = makeStore({ fadeConnectionsEnabled: false });
    renderOpen(store);
    expect(screen.getByRole("checkbox", { name: "Fade Connections" })).not.toBeChecked();
  });

  it("reflects fadeConnectionsEnabled=true as checked", () => {
    const { store } = makeStore({ fadeConnectionsEnabled: true });
    renderOpen(store);
    expect(screen.getByRole("checkbox", { name: "Fade Connections" })).toBeChecked();
  });

  it("calls store.setFadeConnections with the new value on toggle, independent of Snap to Grid", async () => {
    const user = userEvent.setup();
    const { store, setFadeConnections, setSnapToGrid } = makeStore({ fadeConnectionsEnabled: false });
    renderOpen(store);

    await user.click(screen.getByRole("checkbox", { name: "Fade Connections" }));

    expect(setFadeConnections).toHaveBeenCalledWith(true);
    expect(setSnapToGrid).not.toHaveBeenCalled();
  });
});

// R7.5b-2: same posture as the Fade Connections describe block above - only
// the new control is covered.
describe("ViewPopover (R7.5b-2 Orthogonal Routing checkbox)", () => {
  it("reflects orthogonalRouting=false as unchecked", () => {
    const { store } = makeStore({ orthogonalRouting: false });
    renderOpen(store);
    expect(screen.getByRole("checkbox", { name: "Orthogonal Routing" })).not.toBeChecked();
  });

  it("reflects orthogonalRouting=true as checked", () => {
    const { store } = makeStore({ orthogonalRouting: true });
    renderOpen(store);
    expect(screen.getByRole("checkbox", { name: "Orthogonal Routing" })).toBeChecked();
  });

  it("calls store.setOrthogonalConnections with the new value on toggle, independent of Fade Connections", async () => {
    const user = userEvent.setup();
    const { store, setOrthogonalConnections, setFadeConnections } = makeStore({ orthogonalRouting: false });
    renderOpen(store);

    await user.click(screen.getByRole("checkbox", { name: "Orthogonal Routing" }));

    expect(setOrthogonalConnections).toHaveBeenCalledWith(true);
    expect(setFadeConnections).not.toHaveBeenCalled();
  });
});

// R7.5b-3: same posture again - only the new control is covered.
describe("ViewPopover (R7.5b-3 Smart Guides checkbox)", () => {
  it("reflects smartGuides=false as unchecked and smartGuides=true as checked", () => {
    const off = makeStore({ smartGuides: false });
    const { unmount } = renderOpen(off.store);
    expect(screen.getByRole("checkbox", { name: "Smart Guides" })).not.toBeChecked();
    unmount();

    const on = makeStore({ smartGuides: true });
    renderOpen(on.store);
    expect(screen.getByRole("checkbox", { name: "Smart Guides" })).toBeChecked();
  });

  it("calls store.setSmartGuides with the new value on toggle, independent of the other toggles", async () => {
    const user = userEvent.setup();
    const { store, setSmartGuides, setOrthogonalConnections, setFadeConnections } = makeStore({ smartGuides: false });
    renderOpen(store);

    await user.click(screen.getByRole("checkbox", { name: "Smart Guides" }));

    expect(setSmartGuides).toHaveBeenCalledWith(true);
    expect(setOrthogonalConnections).not.toHaveBeenCalled();
    expect(setFadeConnections).not.toHaveBeenCalled();
  });
});

// ADR-002 Workstream 1 ("Branch status and lifecycle"): same posture as the
// other checkbox describe blocks above - only the new control is covered.
describe("ViewPopover (ADR-002 Workstream 1 Focus Accepted Paths checkbox)", () => {
  it("reflects focusAcceptedPaths=false as unchecked and =true as checked", () => {
    const off = makeStore({}, false);
    const { unmount } = renderOpen(off.store);
    expect(screen.getByRole("checkbox", { name: "Focus Accepted Paths" })).not.toBeChecked();
    unmount();

    const on = makeStore({}, true);
    renderOpen(on.store);
    expect(screen.getByRole("checkbox", { name: "Focus Accepted Paths" })).toBeChecked();
  });

  it("calls store.setFocusAcceptedPaths with the new value on toggle, independent of the other toggles", async () => {
    const user = userEvent.setup();
    const { store, setFocusAcceptedPaths, setSmartGuides, setFadeConnections } = makeStore({}, false);
    renderOpen(store);

    await user.click(screen.getByRole("checkbox", { name: "Focus Accepted Paths" }));

    expect(setFocusAcceptedPaths).toHaveBeenCalledWith(true);
    expect(setSmartGuides).not.toHaveBeenCalled();
    expect(setFadeConnections).not.toHaveBeenCalled();
  });
});
