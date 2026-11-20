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
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SceneStore } from "../canvas/sceneStore";
import { HarnessLaunchDialog } from "./HarnessLaunchDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { makeRequestOnlyTransport as makeTransport } from "../../lib/ws/transport.testUtils";

function makeStore(engine: string | null, probe = () => Promise.resolve(engine)) {
  return {
    getScene: () => ({ nodes: [] }),
    getHarnessIsolationEngine: probe,
  } as unknown as SceneStore;
}

function OpenAgentButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("harness-launch", "dialog")}>
      open agent
    </button>
  );
}

async function setup(
  picked: unknown = "C:\\projects\\thing",
  engine: string | null = "docker",
  store: SceneStore = makeStore(engine),
) {
  const user = userEvent.setup();
  const fake = makeTransport();
  fake.request.mockImplementation((_topic: string, intent: string) =>
    intent === "pickLaunchWorkspace" ? Promise.resolve(picked) : Promise.resolve("n7"),
  );
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <OpenAgentButton />
        <HarnessLaunchDialog transport={fake.transport} store={store} />
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

    expect(intents).toContainEqual(["harness", "start", ["summarize the repo", 16, "", "host"]]);
  });

  it("carries a chosen folder into the very first run", async () => {
    const { user, intents } = await setup();

    await user.type(screen.getByLabelText(/what should the agent work on/i), "fix the failing test");
    await user.click(screen.getByRole("button", { name: "Choose folder…" }));
    expect(await screen.findByText("C:\\projects\\thing")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start the agent" }));

    expect(intents).toContainEqual([
      "harness", "start", ["fix the failing test", 16, "C:\\projects\\thing", "host"],
    ]);
  });

  it("a cancelled picker leaves the launch in scratch", async () => {
    const { user, intents } = await setup(null);

    await user.type(screen.getByLabelText(/what should the agent work on/i), "look around");
    await user.click(screen.getByRole("button", { name: "Choose folder…" }));
    // Still scratch: a dismissed picker chose nothing, so nothing changed.
    expect(screen.getByText("Private scratch folder")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Start the agent" }));
    expect(intents).toContainEqual(["harness", "start", ["look around", 16, "", "host"]]);
  });

  it("Enter submits and Shift+Enter does not - the card composer's contract", async () => {
    const { user, intents } = await setup();
    const task = screen.getByLabelText(/what should the agent work on/i);

    await user.type(task, "first line{Shift>}{Enter}{/Shift}second line");
    expect(intents.filter(([, intent]) => intent === "start")).toHaveLength(0);

    await user.type(task, "{Enter}");
    expect(intents).toContainEqual([
      "harness", "start", ["first line\nsecond line", 16, "", "host"],
    ]);
  });


  it("launches the first task as Isolated when Podman is detected", async () => {
    const { user, intents } = await setup(undefined, "podman");
    expect(await screen.findByText(/Podman detected on the backend/)).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Execution profile for new agent" }), "isolated");
    await user.type(screen.getByLabelText(/what should the agent work on/i), "analyze files");
    await user.click(screen.getByRole("button", { name: "Start the agent" }));

    expect(intents).toContainEqual(["harness", "start", ["analyze files", 16, "", "isolated"]]);
  });

  it("does not offer Isolated without Docker or Podman", async () => {
    await setup(undefined, null);
    expect(await screen.findByText(/Docker or Podman is unavailable/)).toBeInTheDocument();
    const isolated = screen.getByRole("option", { name: /Isolated \(Docker \/ Podman\)/ });
    expect(isolated).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Execution profile for new agent" })).toHaveValue("host");
  });

  // Regression #767: a temporary transport failure is not proof that the
  // backend lacks a container engine. Retry without reloading the app.
  it("recovers from a failed first engine check and rechecks on reopening", async () => {
    const getHarnessIsolationEngine = vi.fn()
      .mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValue("docker");
    const { user, intents } = await setup(
      undefined, "docker", makeStore("docker", getHarnessIsolationEngine),
    );

    expect(await screen.findByText(/Could not check Docker or Podman availability/)).toBeInTheDocument();
    expect(getHarnessIsolationEngine).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("option", { name: /Isolated/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Recheck Docker / Podman" }));
    expect(await screen.findByText(/Docker detected on the backend/)).toBeInTheDocument();
    expect(getHarnessIsolationEngine).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Close Agent" }));
    await user.click(screen.getByRole("button", { name: "open agent" }));
    expect(await screen.findByText(/Docker detected on the backend/)).toBeInTheDocument();
    expect(getHarnessIsolationEngine).toHaveBeenCalledTimes(3);

    await user.selectOptions(screen.getByRole("combobox", { name: "Execution profile for new agent" }), "isolated");
    await user.type(screen.getByLabelText(/what should the agent work on/i), "inspect files");
    await user.click(screen.getByRole("button", { name: "Start the agent" }));
    expect(intents).toContainEqual(["harness", "start", ["inspect files", 16, "", "isolated"]]);
  });

  it("allows a fresh probe after an engine is installed later", async () => {
    const getHarnessIsolationEngine = vi.fn()
      .mockResolvedValueOnce(null)
      .mockResolvedValue("podman");
    const { user } = await setup(
      undefined, "docker", makeStore("docker", getHarnessIsolationEngine),
    );

    expect(await screen.findByText(/Docker or Podman is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Isolated/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Recheck Docker / Podman" }));
    expect(await screen.findByText(/Podman detected on the backend/)).toBeInTheDocument();
    expect(getHarnessIsolationEngine).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("option", { name: /Isolated/ })).toBeEnabled();
  });

  it("warns about tasks beyond 20k without starting or clearing the draft", async () => {
    const { user, intents } = await setup();
    const draft = screen.getByLabelText(/what should the agent work on/i);
    const tooLong = "x".repeat(20_000) + "FINAL_INSTRUCTION";
    fireEvent.change(draft, { target: { value: tooLong } });
    await user.click(screen.getByRole("button", { name: "Start the agent" }));

    expect(screen.getByRole("alert")).toHaveTextContent("20,000 characters");
    expect(draft).toHaveValue(tooLong);
    expect(intents.some(([, intent]) => intent === "start")).toBe(false);
  });

  // Regression #768: this is 20,002 UTF-16 units but only 10,001
  // Python/Unicode code points, so the backend accepts it.
  it("allows a first task containing supplementary Unicode under the backend limit", async () => {
    const { user, intents } = await setup();
    const text = "😀".repeat(10_001);
    fireEvent.change(screen.getByLabelText(/what should the agent work on/i), {
      target: { value: text },
    });

    await user.click(screen.getByRole("button", { name: "Start the agent" }));
    expect(intents).toContainEqual(["harness", "start", [text, 16, "", "host"]]);
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
