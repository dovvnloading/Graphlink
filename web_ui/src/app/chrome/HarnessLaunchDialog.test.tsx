/**
 * The agent launcher. Same scaffolding as BuilderLaunchDialog.test.tsx (its
 * sibling and the shape this dialog was built from): OverlayProvider +
 * ReactFlowProvider, a button that opens the overlay, and
 * makeRequestOnlyTransport for the intent call-shape assertions.
 *
 * The cases here are the launch-time friction ones - choosing a workspace
 * before the first run, and Enter submitting the way the agent card's own
 * composer does - since binding a folder afterwards meant the first run of
 * every real piece of work was spent in scratch and then repeated.
 */
import { ReactFlowProvider } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SceneStore } from "../canvas/sceneStore";
import { HarnessLaunchDialog } from "./HarnessLaunchDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { makeRequestOnlyTransport as makeTransport } from "../../lib/ws/transport.testUtils";

function makeStore() {
  return { getScene: () => ({ nodes: [] }) } as unknown as SceneStore;
}

function OpenAgentButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("harness-launch", "dialog")}>
      open agent
    </button>
  );
}

async function setup(picked: unknown = "C:\\projects\\thing") {
  const user = userEvent.setup();
  const fake = makeTransport();
  fake.request.mockImplementation((_topic: string, intent: string) =>
    intent === "pickLaunchWorkspace" ? Promise.resolve(picked) : Promise.resolve("n7"),
  );
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <OpenAgentButton />
        <HarnessLaunchDialog transport={fake.transport} store={makeStore()} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  await user.click(screen.getByRole("button", { name: "open agent" }));
  return { user, ...fake };
}

describe("HarnessLaunchDialog", () => {
  it("starts in scratch when no folder is chosen", async () => {
    const { user, intents } = await setup();

    await user.type(screen.getByLabelText(/what should the agent work on/i), "summarize the repo");
    await user.click(screen.getByRole("button", { name: "Start the agent" }));

    expect(intents).toContainEqual(["harness", "start", ["summarize the repo", 16, ""]]);
  });

  it("carries a chosen folder into the very first run", async () => {
    const { user, intents } = await setup();

    await user.type(screen.getByLabelText(/what should the agent work on/i), "fix the failing test");
    await user.click(screen.getByRole("button", { name: "Choose folder…" }));
    expect(await screen.findByText("C:\\projects\\thing")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start the agent" }));

    expect(intents).toContainEqual([
      "harness", "start", ["fix the failing test", 16, "C:\\projects\\thing"],
    ]);
  });

  it("a cancelled picker leaves the launch in scratch", async () => {
    const { user, intents } = await setup(null);

    await user.type(screen.getByLabelText(/what should the agent work on/i), "look around");
    await user.click(screen.getByRole("button", { name: "Choose folder…" }));
    // Still scratch: a dismissed picker chose nothing, so nothing changed.
    expect(screen.getByText("Private scratch folder")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start the agent" }));
    expect(intents).toContainEqual(["harness", "start", ["look around", 16, ""]]);
  });

  it("Enter submits and Shift+Enter does not - the card composer's contract", async () => {
    const { user, intents } = await setup();
    const task = screen.getByLabelText(/what should the agent work on/i);

    await user.type(task, "first line{Shift>}{Enter}{/Shift}second line");
    expect(intents.filter(([, intent]) => intent === "start")).toHaveLength(0);

    await user.type(task, "{Enter}");
    expect(intents).toContainEqual([
      "harness", "start", ["first line\nsecond line", 16, ""],
    ]);
  });

  it("describes what the agent can actually do, approvals included", async () => {
    // The hint is the only thing anyone reads before granting the first
    // tool call, so it has to name the real capability surface - it used to
    // say "read-only file tools" about an agent that writes files and runs
    // shell commands.
    await setup();
    const hint = screen.getByText(/reads and writes files/i);
    expect(hint).toHaveTextContent(/shell and Python/i);
    expect(hint).toHaveTextContent(/asks before anything that changes your machine/i);
  });
});
