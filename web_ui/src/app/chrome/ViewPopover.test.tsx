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

function makeStore(sceneOverrides: Partial<typeof initialSceneState> = {}) {
  const listeners = new Set<() => void>();
  const scene = { ...initialSceneState, ...sceneOverrides };
  const setFadeConnections = vi.fn();
  const setSnapToGrid = vi.fn();
  const store = {
    subscribe: (l: () => void) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    getScene: () => scene,
    getGrid: () => initialGridState,
    getDragConfig: () => initialDragSpeedState,
    getFontConfig: () => initialFontControlState,
    setDragFactor: vi.fn(),
    setGridSize: vi.fn(),
    setGridOpacityPercent: vi.fn(),
    setGridStyle: vi.fn(),
    setGridColor: vi.fn(),
    setSnapToGrid,
    setFadeConnections,
    setFontFamily: vi.fn(),
    setFontSize: vi.fn(),
    setFontColor: vi.fn(),
  };
  return { store, setFadeConnections, setSnapToGrid };
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
