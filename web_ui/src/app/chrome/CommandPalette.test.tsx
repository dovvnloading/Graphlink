import { ReactFlowProvider } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CommandPalette } from "./CommandPalette";
import { initialSceneState } from "../canvas/sceneStore";
import { OverlayProvider, useOverlays } from "../overlays/overlays";

function makeStore() {
  const scene = { ...initialSceneState, nodes: [{ id: "n0", x: 0, y: 0, title: "A", kind: "placeholder" }] };
  return {
    subscribe: () => () => {},
    getScene: () => scene,
    organizeNodes: () => {},
  };
}

function OpenPaletteButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.toggle("palette", "dialog")}>
      open palette
    </button>
  );
}

function AboutOpenProbe() {
  const overlays = useOverlays();
  return <span data-testid="about-open">{String(overlays.isOpen("about"))}</span>;
}

function setup() {
  const user = userEvent.setup();
  const store = makeStore();
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <OpenPaletteButton />
        <AboutOpenProbe />
        {/* @ts-expect-error - test double */}
        <CommandPalette store={store} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  return user;
}

describe("CommandPalette", () => {
  it("is closed until the palette surface opens", () => {
    setup();
    expect(screen.queryByLabelText("Search commands")).toBeNull();
  });

  it("opens, filters by typed text, and closes after executing a command", async () => {
    const user = setup();
    await user.click(screen.getByText("open palette"));
    const input = screen.getByLabelText("Search commands");
    expect(screen.getByText("Fit All to View")).toBeInTheDocument();

    await user.type(input, "organize");
    expect(screen.getByText("Organize Nodes")).toBeInTheDocument();
    expect(screen.queryByText("Fit All to View")).toBeNull();

    await user.click(screen.getByText("Organize Nodes"));
    expect(screen.queryByLabelText("Search commands")).toBeNull();
  });

  it("ArrowDown/Enter selects and executes the highlighted command", async () => {
    const user = setup();
    await user.click(screen.getByText("open palette"));
    await user.type(screen.getByLabelText("Search commands"), "zoom");
    // "Zoom In" then "Zoom Out" per the registry order.
    await user.keyboard("{ArrowDown}{Enter}");
    expect(screen.queryByLabelText("Search commands")).toBeNull();
  });

  it("executing an 'open X' command leaves that surface open, not just closed", async () => {
    // Regression: execute() used to call command.run() (which opens the
    // target overlay) THEN overlays.close() unconditionally - since the
    // overlay system is single-open, that close() always won and silently
    // undid the open. Every "Open ..." command was affected.
    const user = setup();
    expect(screen.getByTestId("about-open")).toHaveTextContent("false");
    await user.click(screen.getByText("open palette"));
    await user.type(screen.getByLabelText("Search commands"), "About");
    await user.click(screen.getByText("Open About"));
    expect(screen.queryByLabelText("Search commands")).toBeNull();
    expect(screen.getByTestId("about-open")).toHaveTextContent("true");
  });
});
