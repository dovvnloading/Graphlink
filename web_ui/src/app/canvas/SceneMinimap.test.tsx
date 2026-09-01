import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReactFlowProvider, type useReactFlow as UseReactFlowType } from "@xyflow/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import { initialSceneState } from "./sceneStore";
import type { SceneStore } from "./sceneStore";

type SetCenterCall = [number, number, { zoom?: number; duration?: number } | undefined];
const setCenterCalls: SetCenterCall[] = [];

// Same useReactFlow-wrapping shape GlobalSearchDialog.test.tsx and
// BuilderLaunchDialog.test.tsx already use: every real export stays
// functional, only the call being asserted on is intercepted.
vi.mock("@xyflow/react", async (importOriginal) => {
  const original = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...original,
    useReactFlow: (...args: Parameters<typeof UseReactFlowType>) => {
      const real = original.useReactFlow(...args);
      return {
        ...real,
        getZoom: () => 0.75,
        setCenter: (x: number, y: number, options?: { zoom?: number; duration?: number }) => {
          setCenterCalls.push([x, y, options]);
        },
      };
    },
  };
});

import { SceneMinimap } from "./SceneMinimap";

function node(overrides: Partial<SceneNodeRow> = {}): SceneNodeRow {
  return {
    id: "n0",
    x: 0,
    y: 0,
    title: "",
    kind: "chat",
    ...overrides,
  } as unknown as SceneNodeRow;
}

function makeStore(nodes: SceneNodeRow[]): SceneStore {
  const scene = { ...initialSceneState, nodes };
  return {
    subscribe: () => () => {},
    getScene: () => scene,
  } as unknown as SceneStore;
}

function renderMap(nodes: SceneNodeRow[]) {
  return render(
    <ReactFlowProvider>
      <SceneMinimap store={makeStore(nodes)} />
    </ReactFlowProvider>,
  );
}

describe("SceneMinimap", () => {
  beforeEach(() => {
    setCenterCalls.length = 0;
    window.localStorage.clear();
  });

  it("renders nothing at all on an empty canvas", () => {
    const { container } = renderMap([]);
    // The stock minimap drew its full box over a fresh session, mapping
    // nothing. There is no panel to put away because there is no panel.
    expect(container.querySelector(".scene-minimap-panel")).toBeNull();
  });

  it("counts the nodes it is mapping", () => {
    renderMap([node({ id: "a" }), node({ id: "b" })]);
    expect(screen.getByText("2 nodes")).toBeInTheDocument();
  });

  it("says node, not nodes, when there is one", () => {
    renderMap([node({ id: "a" })]);
    expect(screen.getByText("1 node")).toBeInTheDocument();
  });

  describe("the waiting chip", () => {
    it("stays hidden when nothing needs a decision", () => {
      renderMap([node({ id: "a" }), node({ id: "b", builderStatus: "running" })]);
      expect(screen.queryByText(/waiting/)).toBeNull();
    });

    it("counts every parked node", () => {
      renderMap([
        node({ id: "a", harnessAwaitingQuestion: true }),
        node({ id: "b", builderAwaitingToolApproval: true }),
        node({ id: "c" }),
      ]);
      expect(screen.getByText("2 waiting")).toBeInTheDocument();
    });

    it("jumps to the first parked node at the CURRENT zoom", async () => {
      const user = userEvent.setup();
      renderMap([
        node({ id: "a" }),
        node({ id: "b", x: 400, y: 250, harnessAwaitingApproval: true }),
      ]);

      await user.click(screen.getByText("1 waiting"));

      expect(setCenterCalls).toHaveLength(1);
      const [x, y, options] = setCenterCalls[0];
      expect([x, y]).toEqual([400, 250]);
      // React Flow's setCenter defaults `zoom` to maxZoom when it is
      // omitted, so leaving it out sends the canvas to 250% on every jump.
      // Passing the current zoom is what makes this a pan.
      expect(options?.zoom).toBe(0.75);
    });
  });

  describe("collapsing", () => {
    it("puts the map away and keeps the count", async () => {
      const user = userEvent.setup();
      const { container } = renderMap([node({ id: "a" })]);
      expect(container.querySelector(".scene-minimap")).not.toBeNull();

      await user.click(screen.getByRole("button", { name: "Collapse minimap" }));

      expect(container.querySelector(".scene-minimap")).toBeNull();
      expect(screen.getByText("1 node")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Expand minimap" })).toBeInTheDocument();
    });

    it("remembers the choice", async () => {
      const user = userEvent.setup();
      renderMap([node({ id: "a" })]);
      await user.click(screen.getByRole("button", { name: "Collapse minimap" }));
      expect(window.localStorage.getItem("graphlink.minimap.collapsed")).toBe("1");
    });

    it("opens collapsed when that is what was remembered", () => {
      window.localStorage.setItem("graphlink.minimap.collapsed", "1");
      const { container } = renderMap([node({ id: "a" })]);
      expect(container.querySelector(".scene-minimap")).toBeNull();
    });

    it("survives storage being unavailable", () => {
      // Private windows and blocked site data both throw on access. The map
      // should forget the preference, not take the canvas down with it.
      const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      const { container } = renderMap([node({ id: "a" })]);
      expect(container.querySelector(".scene-minimap")).not.toBeNull();
      getItem.mockRestore();
    });
  });
});
