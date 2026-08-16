/**
 * stage 8.7 rebuild. Mirrors GlobalSearchDialog.test.tsx's own useReactFlow
 * wrapping (every real pan/zoom export stays functional; only setCenter is
 * intercepted) for asserting the post-launch focus call's exact arguments,
 * and CustomSelect.test.tsx's own "click the trigger by its ariaLabel, then
 * click the option by its label" interaction shape for the recipe picker -
 * userEvent.selectOptions no longer applies now that this is not a native
 * <select>.
 */
import { ReactFlowProvider, type useReactFlow as UseReactFlowType } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SceneStore } from "../canvas/sceneStore";

type SetCenterCall = [number, number, { zoom?: number; duration?: number } | undefined];
const setCenterCalls: SetCenterCall[] = [];

vi.mock("@xyflow/react", async (importOriginal) => {
  const original = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...original,
    useReactFlow: (...args: Parameters<typeof UseReactFlowType>) => {
      const real = original.useReactFlow(...args);
      return {
        ...real,
        setCenter: (x: number, y: number, options?: { zoom?: number; duration?: number }) => {
          setCenterCalls.push([x, y, options]);
          return real.setCenter(x, y, options);
        },
      };
    },
  };
});

import { BuilderLaunchDialog } from "./BuilderLaunchDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

function makeTransport() {
  const intents: unknown[][] = [];
  const request = vi.fn<(topic: string, intent: string, args?: unknown[]) => Promise<unknown>>();
  const transport = {
    subscribe: () => () => {},
    intent: () => {},
    fireIntent: () => {},
    request: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
      return request(topic, intent, args);
    },
  } as unknown as WsTransport;
  return { transport, intents, request };
}

// Only getScene() is exercised (reading the newly-created node's position
// for the post-launch setCenter call) - a minimal fake, the same posture
// makeTransport() takes for WsTransport above.
function makeStore(nodes: Array<{ id: string; x: number; y: number }> = []) {
  return {
    getScene: () => ({ nodes }),
  } as unknown as SceneStore;
}

function OpenBuilderButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("builder-launch", "dialog")}>
      open builder
    </button>
  );
}

async function setup(startResult: unknown = "n42", recipes: unknown[] = [], nodes: Array<{ id: string; x: number; y: number }> = []) {
  const user = userEvent.setup();
  const fake = makeTransport();
  const store = makeStore(nodes);
  // The dialog fires listRecipes on open - dispatch by intent so each
  // test's start-result expectation is never consumed by the list call.
  fake.request.mockImplementation((topic: string, intent: string) =>
    intent === "listRecipes"
      ? Promise.resolve({ recipes })
      : intent === "deleteRecipe"
        ? Promise.resolve(true)
        : startResult instanceof Error
          ? Promise.reject(startResult)
          : Promise.resolve(startResult),
  );
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <OpenBuilderButton />
        <BuilderLaunchDialog transport={fake.transport} store={store} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  await user.click(screen.getByRole("button", { name: "open builder" }));
  return { user, ...fake };
}

async function pickRecipe(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: "Recipe" }));
  await user.click(screen.getByRole("button", { name: new RegExp("^" + name) }));
}

describe("BuilderLaunchDialog", () => {
  beforeEach(() => {
    setCenterCalls.length = 0;
  });

  it("submits the goal with mode and default (Standard) budgets through builder/start", async () => {
    const { user, intents } = await setup();

    await user.type(screen.getByLabelText(/what should the builder construct/i), "chart solar trends");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));

    expect(intents).toContainEqual([
      "builder", "start", ["chart solar trends", "copilot", 12, 150_000, 900, null],
    ]);
  });

  it("selecting a recipe enables launch without a typed goal and passes the name", async () => {
    const { user, intents } = await setup("n9", [
      { name: "Research and summarize", description: "d", steps: [], mode: "copilot", builtIn: true },
    ]);

    await pickRecipe(user, "Research and summarize");
    await user.click(screen.getByRole("button", { name: "Start from recipe" }));

    expect(intents).toContainEqual([
      "builder", "start", ["", "copilot", 12, 150_000, 900, "Research and summarize"],
    ]);
  });

  it("the start button is disabled for a blank goal", async () => {
    await setup();
    expect(screen.getByRole("button", { name: "Plan the build" })).toBeDisabled();
  });

  it("selecting autopilot surfaces the disclosure before launch", async () => {
    const { user } = await setup();

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /autopilot/i }));

    expect(screen.getByRole("note")).toHaveTextContent(/execute code without asking/i);
    expect(screen.getByRole("note")).toHaveTextContent(/network access will still ask/i);
  });

  it("marks only the chosen oversight row selected (drives its highlight)", async () => {
    const { user } = await setup();
    const copilot = screen.getByRole("radio", { name: /co-pilot/i });
    const autopilot = screen.getByRole("radio", { name: /autopilot/i });

    expect(copilot.closest("label")).toHaveClass("selected");
    expect(autopilot.closest("label")).not.toHaveClass("selected");

    await user.click(autopilot);

    expect(autopilot.closest("label")).toHaveClass("selected");
    expect(copilot.closest("label")).not.toHaveClass("selected");
  });

  it("shows the selected recipe's own description and step list", async () => {
    const { user } = await setup("n9", [
      {
        name: "Research and summarize",
        description: "Researches a topic, then writes a summary.",
        steps: ["Create a web research node", "Write the summary note"],
        mode: "copilot",
        builtIn: true,
      },
    ]);

    await pickRecipe(user, "Research and summarize");

    expect(
      await screen.findByText("Researches a topic, then writes a summary."),
    ).toBeInTheDocument();
    expect(screen.getByText("Create a web research node")).toBeInTheDocument();
    expect(screen.getByText("Write the summary note")).toBeInTheDocument();
  });

  it("offers to delete a saved (non-built-in) recipe, and refreshes the list on success", async () => {
    const { user, intents, request } = await setup("n9", [
      { name: "My recipe", description: "d", steps: ["one"], mode: "copilot", builtIn: false },
    ]);

    await pickRecipe(user, "My recipe");
    expect(screen.getByRole("button", { name: "Delete this recipe" })).toBeInTheDocument();

    request.mockImplementation((topic: string, intent: string) =>
      intent === "listRecipes" ? Promise.resolve({ recipes: [] }) : Promise.resolve(true),
    );
    await user.click(screen.getByRole("button", { name: "Delete this recipe" }));

    expect(intents).toContainEqual(["builder", "deleteRecipe", ["My recipe"]]);
    // The list is re-fetched and the selection clears back to from-scratch.
    expect(await screen.findByText("Describe a build yourself, or pick a saved recipe.")).toBeInTheDocument();
  });

  it("hides the delete affordance for a built-in recipe", async () => {
    const { user } = await setup("n9", [
      { name: "Research and summarize", description: "d", steps: [], mode: "copilot", builtIn: true },
    ]);

    await pickRecipe(user, "Research and summarize");

    expect(screen.queryByRole("button", { name: "Delete this recipe" })).not.toBeInTheDocument();
  });

  it("defaults to the Standard budget preset, and a preset click updates the submitted budgets", async () => {
    const { user, intents } = await setup();

    expect(screen.getByRole("button", { name: "Standard" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/12 steps · 150k tokens · 15 min/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Quick" }));
    expect(screen.getByRole("button", { name: "Quick" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Standard" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/6 steps · 50k tokens · 5 min/)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/what should the builder construct/i), "goal");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));

    expect(intents).toContainEqual(["builder", "start", ["goal", "copilot", 6, 50_000, 300, null]]);
  });

  it("clamps an out-of-range Advanced max-steps value on blur rather than silently reverting a typed 0", async () => {
    const { user, intents } = await setup();

    await user.click(screen.getByText("Advanced"));
    const stepsInput = screen.getByLabelText("Max steps");
    await user.clear(stepsInput);
    await user.type(stepsInput, "0");
    // Mid-typing, the raw value is preserved (not silently coerced back to
    // the default the moment the field reads 0 or empty).
    expect(stepsInput).toHaveValue(0);
    await user.tab(); // blur

    expect(stepsInput).toHaveValue(1); // clamped to MIN_STEPS, not reverted to 12

    await user.type(screen.getByLabelText(/what should the builder construct/i), "goal");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));
    expect(intents).toContainEqual(["builder", "start", ["goal", "copilot", 1, 150_000, 900, null]]);
  });

  it("a null start result (backend refused) shows an error instead of closing", async () => {
    const { user } = await setup(null);

    await user.type(screen.getByLabelText(/what should the builder construct/i), "goal");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not start/i);
    expect(setCenterCalls).toHaveLength(0);
  });

  it("a successful start closes the dialog and centers the viewport on the new plan node", async () => {
    const { user } = await setup("n7", [], [{ id: "n7", x: 300, y: 200 }]);

    await user.type(screen.getByLabelText(/what should the builder construct/i), "goal");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));

    expect(
      await screen.findByRole("button", { name: "open builder" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/what should the builder construct/i)).not.toBeInTheDocument();

    expect(setCenterCalls).toHaveLength(1);
    const [x, y] = setCenterCalls[0];
    expect(x).toBe(300 + 180);
    expect(y).toBe(200 + 90);
  });
});
