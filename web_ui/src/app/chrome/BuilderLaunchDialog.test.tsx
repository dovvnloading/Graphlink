import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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

function OpenBuilderButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("builder-launch", "dialog")}>
      open builder
    </button>
  );
}

async function setup() {
  const user = userEvent.setup();
  const fake = makeTransport();
  render(
    <OverlayProvider>
      <OpenBuilderButton />
      <BuilderLaunchDialog transport={fake.transport} />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole("button", { name: "open builder" }));
  return { user, ...fake };
}

describe("BuilderLaunchDialog", () => {
  it("submits the goal with mode and default budgets through builder/start", async () => {
    const { user, intents, request } = await setup();
    request.mockResolvedValueOnce("n42");

    await user.type(screen.getByLabelText(/what should the builder construct/i), "chart solar trends");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));

    expect(intents).toContainEqual([
      "builder", "start", ["chart solar trends", "copilot", 12, 150_000, 900],
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

  it("a null start result (backend refused) shows an error instead of closing", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce(null);

    await user.type(screen.getByLabelText(/what should the builder construct/i), "goal");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not start/i);
  });

  it("a successful start closes the dialog", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce("n7");

    await user.type(screen.getByLabelText(/what should the builder construct/i), "goal");
    await user.click(screen.getByRole("button", { name: "Plan the build" }));

    expect(
      await screen.findByRole("button", { name: "open builder" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/what should the builder construct/i)).not.toBeInTheDocument();
  });
});
