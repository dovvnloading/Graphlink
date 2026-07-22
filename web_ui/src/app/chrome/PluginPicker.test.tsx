import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PluginPicker } from "./PluginPicker";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

const snapshot = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  categories: [
    {
      name: "Branch Foundations",
      description: "Core branch scaffolding.",
      plugins: [
        { name: "System Prompt", description: "Adds a system-prompt node." },
        { name: "Conversation Node", description: "Adds a linear chat node." },
      ],
    },
    {
      name: "Build & Execution",
      description: "Code generation and execution.",
      plugins: [{ name: "Py-Coder", description: "Python workspace." }],
    },
  ],
};

function makeTransport() {
  const intents: unknown[][] = [];
  let listener: ((payload: Record<string, unknown>) => void) | null = null;
  const transport = {
    subscribe: (_topic: string, l: (payload: Record<string, unknown>) => void) => {
      listener = l;
      return () => {
        listener = null;
      };
    },
    intent: (topic: string, intent: string, args: unknown[]) => {
      intents.push([topic, intent, args]);
    },
  } as unknown as WsTransport;
  return {
    transport,
    intents,
    push: (payload: Record<string, unknown>) => listener?.(payload),
  };
}

function OpenPluginsButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.toggle("plugins", "popover")}>
      open plugins
    </button>
  );
}

function setup() {
  const user = userEvent.setup();
  const fake = makeTransport();
  render(
    <OverlayProvider>
      <OpenPluginsButton />
      <PluginPicker transport={fake.transport} />
    </OverlayProvider>,
  );
  act(() => fake.push(snapshot));
  return { user, ...fake };
}

describe("PluginPicker", () => {
  it("renders backend categories and plugins once the popover opens", async () => {
    const { user } = setup();
    expect(screen.queryByText("Categories")).toBeNull();

    await user.click(screen.getByText("open plugins"));
    // The active category's name appears twice by design: rail button + pane title.
    expect(screen.getByRole("button", { name: "Branch Foundations" })).toBeInTheDocument();
    expect(screen.getByText("System Prompt")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Build & Execution/ }));
    expect(screen.getByText("Py-Coder")).toBeInTheDocument();
  });

  it("selecting a plugin fires executePlugin and closes the popover", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open plugins"));

    await user.click(screen.getByText("System Prompt"));
    expect(intents).toContainEqual(["app-plugins", "executePlugin", ["System Prompt"]]);
    // Close-on-select: the popover dismisses so the resulting notification
    // banner is seen unobstructed (matching the legacy popup's behavior).
    expect(screen.queryByText("Categories")).toBeNull();
  });
});
