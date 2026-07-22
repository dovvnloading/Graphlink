import { ReactFlowProvider } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SearchOverlay } from "./SearchOverlay";
import { initialSceneState } from "../canvas/sceneStore";
import { OverlayProvider, useOverlays } from "../overlays/overlays";

function makeStore() {
  const scene = {
    ...initialSceneState,
    nodes: [
      { id: "n0", x: 0, y: 0, title: "Alpha node", kind: "placeholder" },
      { id: "n1", x: 100, y: 100, title: "Beta node", kind: "placeholder" },
      { id: "n2", x: 200, y: 200, title: "Another alpha", kind: "placeholder" },
    ],
  };
  return { subscribe: () => () => {}, getScene: () => scene };
}

function OpenSearchButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.toggle("search", "popover")}>
      open search
    </button>
  );
}

function setup() {
  const user = userEvent.setup();
  const store = makeStore();
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <OpenSearchButton />
        {/* @ts-expect-error - test double */}
        <SearchOverlay store={store} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  return user;
}

describe("SearchOverlay", () => {
  it("is closed until the search surface opens", () => {
    setup();
    expect(screen.queryByLabelText("Search the canvas")).toBeNull();
  });

  it("counts matches against live node titles and updates on Enter/Shift+Enter", async () => {
    const user = setup();
    await user.click(screen.getByText("open search"));
    const input = screen.getByLabelText("Search the canvas");
    await user.type(input, "alpha");
    expect(screen.getByText("0 / 2")).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
  });

  it("shows 0/0 for a query with no matches", async () => {
    const user = setup();
    await user.click(screen.getByText("open search"));
    await user.type(screen.getByLabelText("Search the canvas"), "zzz");
    expect(screen.getByText("0 / 0")).toBeInTheDocument();
  });

  it("close button closes the overlay", async () => {
    const user = setup();
    await user.click(screen.getByText("open search"));
    await user.click(screen.getByLabelText("Close (Esc)"));
    expect(screen.queryByLabelText("Search the canvas")).toBeNull();
  });
});
