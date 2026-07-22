import { ReactFlowProvider } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PinOverlay } from "./PinOverlay";
import { initialSceneState } from "../canvas/sceneStore";
import { OverlayProvider, useOverlays } from "../overlays/overlays";

function makeStore(pins: Array<{ id: string; title: string; note: string; x: number; y: number }>) {
  const listeners = new Set<() => void>();
  const scene = { ...initialSceneState, pins };
  const addPin = vi.fn();
  const updatePin = vi.fn();
  const removePin = vi.fn();
  const store = {
    subscribe: (l: () => void) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    getScene: () => scene,
    addPin,
    updatePin,
    removePin,
  };
  return { store, addPin, updatePin, removePin };
}

// PinOverlay renders via <Popover name="pins">, which only mounts content
// while open - force it open through the real overlay context so the test
// exercises the actual gate, not a bypass.
function OpenPinsOnMount({ children }: { children: React.ReactNode }) {
  const overlays = useOverlays();
  if (!overlays.isOpen("pins")) overlays.open("pins", "popover");
  return <>{children}</>;
}

function renderOpen(store: unknown) {
  return render(
    <OverlayProvider>
      <ReactFlowProvider>
        <OpenPinsOnMount>
          {/* @ts-expect-error - test double */}
          <PinOverlay store={store} />
        </OpenPinsOnMount>
      </ReactFlowProvider>
    </OverlayProvider>,
  );
}

describe("PinOverlay", () => {
  it("shows an empty state with no pins", () => {
    const { store } = makeStore([]);
    renderOpen(store);
    expect(screen.getByText("No pins yet.")).toBeInTheDocument();
  });

  it("filters pins by title or note via search", async () => {
    const user = userEvent.setup();
    const { store } = makeStore([
      { id: "p1", title: "Origin", note: "start here", x: 0, y: 0 },
      { id: "p2", title: "Endpoint", note: "", x: 10, y: 10 },
    ]);
    renderOpen(store);
    await user.type(screen.getByLabelText("Search pins"), "origin");
    expect(screen.getByText("Origin")).toBeInTheDocument();
    expect(screen.queryByText("Endpoint")).toBeNull();
  });

  it("edit flow: opens editor, validates empty title, saves via updatePin", async () => {
    const user = userEvent.setup();
    const { store, updatePin } = makeStore([{ id: "p1", title: "Origin", note: "n", x: 0, y: 0 }]);
    renderOpen(store);
    await user.click(screen.getByLabelText("Edit Origin"));
    const titleInput = screen.getByLabelText("Pin title");
    await user.clear(titleInput);
    await user.click(screen.getByText("Save"));
    expect(screen.getByText("A title is required")).toBeInTheDocument();
    expect(updatePin).not.toHaveBeenCalled();

    await user.type(titleInput, "Renamed");
    await user.click(screen.getByText("Save"));
    expect(updatePin).toHaveBeenCalledWith("p1", "Renamed", "n");
  });

  it("remove calls the intent", async () => {
    const user = userEvent.setup();
    const { store, removePin } = makeStore([{ id: "p1", title: "Origin", note: "", x: 0, y: 0 }]);
    renderOpen(store);
    await user.click(screen.getByLabelText("Remove Origin"));
    expect(removePin).toHaveBeenCalledWith("p1");
  });
});
