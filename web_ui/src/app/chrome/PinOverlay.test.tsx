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

  it("R8a finding #16: Escape while editing a pin cancels the edit WITHOUT closing the whole Pins popover", async () => {
    // Neither the title input nor the note textarea had ANY onKeyDown
    // before this fix - Escape fell through untouched to overlays.tsx's
    // own document-level handler, which closed the entire Popover (the
    // same bug class already fixed for Chat Library's rename input, just
    // missed here since this file had no existing "Escape" handler to
    // find via a grep for one - there was nothing to find, only something
    // to add).
    const user = userEvent.setup();
    const { store, updatePin } = makeStore([{ id: "p1", title: "Origin", note: "n", x: 0, y: 0 }]);
    renderOpen(store);
    await user.click(screen.getByLabelText("Edit Origin"));
    await user.clear(screen.getByLabelText("Pin title"));
    await user.type(screen.getByLabelText("Pin title"), "Throwaway");

    await user.keyboard("{Escape}");

    expect(updatePin).not.toHaveBeenCalled();
    // The edit reverted (back to the read-only row, original title intact)...
    expect(screen.queryByLabelText("Pin title")).toBeNull();
    expect(screen.getByText("Origin")).toBeInTheDocument();
    // ...and the popover itself is still open, unlike before this fix.
    expect(screen.getByText("PINS")).toBeInTheDocument();
  });

  it("R8a finding #16: Escape in the note textarea also cancels the edit without closing the popover", async () => {
    const user = userEvent.setup();
    const { store, updatePin } = makeStore([{ id: "p1", title: "Origin", note: "n", x: 0, y: 0 }]);
    renderOpen(store);
    await user.click(screen.getByLabelText("Edit Origin"));

    await user.click(screen.getByLabelText("Pin note"));
    await user.keyboard("{Escape}");

    expect(updatePin).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Pin note")).toBeNull();
    expect(screen.getByText("PINS")).toBeInTheDocument();
  });

  it("remove calls the intent", async () => {
    const user = userEvent.setup();
    const { store, removePin } = makeStore([{ id: "p1", title: "Origin", note: "", x: 0, y: 0 }]);
    renderOpen(store);
    await user.click(screen.getByLabelText("Remove Origin"));
    expect(removePin).toHaveBeenCalledWith("p1");
  });
});
