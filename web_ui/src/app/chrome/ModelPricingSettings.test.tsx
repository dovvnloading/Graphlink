import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { ModelPricingSettings } from "./ModelPricingSettings";

const effective = [
  { modelId: "gpt-4o-mini", provider: "OpenAI-Compatible", inputPerMillion: 0.15,
    outputPerMillion: 0.6, source: "built-in" },
  { modelId: "tinyllama:latest", provider: "ollama", inputPerMillion: 0,
    outputPerMillion: 0, source: "local" },
  { modelId: "/models/tiny.gguf", provider: "llama.cpp", inputPerMillion: 0,
    outputPerMillion: 0, source: "local" },
  { modelId: "unknown-model", provider: "Custom", inputPerMillion: null,
    outputPerMillion: null, source: "unknown" },
];

function setup(overrides: Record<string, unknown> = {}) {
  const request = vi.fn(async () => ({}));
  const user = userEvent.setup();
  const state = { pricingOverrides: [], effectiveModelPrices: effective, ...overrides } as unknown as AppSettingsState;
  render(<ModelPricingSettings state={state} transport={{ request } as unknown as WsTransport} />);
  return { user, request };
}

describe("ModelPricingSettings", () => {
  it("lists effective input/output prices, provenance, unknown prices and free local models", () => {
    setup();
    const list = screen.getByRole("list", { name: "Effective model prices" });
    const cloud = within(list).getByText("gpt-4o-mini").closest("li");
    expect(cloud).not.toBeNull();
    expect(within(cloud!).getByText("OpenAI-Compatible · Built-in table")).toBeInTheDocument();
    expect(within(cloud!).getByText("Input: $0.15 · Output: $0.6")).toBeInTheDocument();
    expect(within(list).getByText("Custom · No known price")).toBeInTheDocument();
    expect(within(list).getByText("Input: Unknown · Output: Unknown")).toBeInTheDocument();
    expect(within(list).getAllByText(/Local · free/)).toHaveLength(2);
    expect(screen.getByText(/always have \$0 token cost/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit pricing for ollama/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit pricing for llama.cpp/ })).not.toBeInTheDocument();
  });

  it("prefills the built-in rate for editing and saves exact-ID overrides using Enter", async () => {
    const { user, request } = setup();
    await user.click(screen.getByRole("button", { name: "Edit pricing for OpenAI-Compatible / gpt-4o-mini" }));
    const name = screen.getByRole("textbox", { name: "Model ID" });
    const input = screen.getByRole("spinbutton", { name: "Input (USD / million tokens)" });
    const output = screen.getByRole("spinbutton", { name: "Output (USD / million tokens)" });
    expect(name).toHaveValue("gpt-4o-mini");
    expect(input).toHaveValue(0.15);
    expect(output).toHaveValue(0.6);
    await user.clear(input);
    await user.type(input, "2.5");
    await user.clear(output);
    await user.type(output, "6.75{Enter}");
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "app-settings", "setPricingOverrides", [{ "gpt-4o-mini": { input: 2.5, output: 6.75 } }],
    ));
    expect(await screen.findByRole("status")).toHaveTextContent("Model pricing saved");
  });

  it("adds a custom model and rejects blank, negative and non-finite drafts before network writes", async () => {
    const { user, request } = setup();
    await user.click(screen.getByRole("button", { name: "Save override" }));
    expect(screen.getByRole("alert")).toHaveTextContent("non-empty model ID");
    expect(request).not.toHaveBeenCalled();

    await user.type(screen.getByRole("textbox", { name: "Model ID" }), "other-model");
    await user.click(screen.getByRole("button", { name: "Save override" }));
    expect(screen.getByRole("alert")).toHaveTextContent("finite, non-negative");
    expect(request).not.toHaveBeenCalled();
    const input = screen.getByRole("spinbutton", { name: "Input (USD / million tokens)" });
    const output = screen.getByRole("spinbutton", { name: "Output (USD / million tokens)" });
    await user.type(input, "-1");
    await user.type(output, "0");
    await user.click(screen.getByRole("button", { name: "Save override" }));
    expect(request).not.toHaveBeenCalled();
    await user.clear(input);
    await user.type(input, "0");
    await user.click(screen.getByRole("button", { name: "Save override" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "app-settings", "setPricingOverrides", [{ "other-model": { input: 0, output: 0 } }],
    ));
  });

  it("removes only the chosen override and keeps other saved prices", async () => {
    const { user, request } = setup({
      pricingOverrides: [
        { modelId: "gpt-4o-mini", inputPerMillion: 3, outputPerMillion: 4 },
        { modelId: "orphan", inputPerMillion: 1, outputPerMillion: 2 },
      ],
      effectiveModelPrices: [
        { ...effective[0], inputPerMillion: 3, outputPerMillion: 4, source: "override" },
        { modelId: "orphan", provider: "", inputPerMillion: 1, outputPerMillion: 2, source: "override" },
      ],
    });
    await user.click(screen.getByRole("button", { name: "Remove override for OpenAI-Compatible / gpt-4o-mini" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "app-settings", "setPricingOverrides", [{ orphan: { input: 1, output: 2 } }],
    ));
    expect(await screen.findByRole("status")).toHaveTextContent("Price override removed");
  });

  it("never changes local costs, but allows removing a legacy local override", async () => {
    const { user, request } = setup({
      pricingOverrides: [{ modelId: "tinyllama:latest", inputPerMillion: 3, outputPerMillion: 4 }],
    });
    expect(screen.queryByRole("button", { name: "Edit pricing for ollama / tinyllama:latest" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Remove override for ollama / tinyllama:latest" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "app-settings", "setPricingOverrides", [{}],
    ));
  });

  it("preserves the draft on a backend rejection, announcing the error", async () => {
    const { user, request } = setup();
    request.mockRejectedValueOnce(new Error("Model ID is invalid"));
    await user.type(screen.getByRole("textbox", { name: "Model ID" }), "bad-model");
    await user.type(screen.getByRole("spinbutton", { name: "Input (USD / million tokens)" }), "1");
    await user.type(screen.getByRole("spinbutton", { name: "Output (USD / million tokens)" }), "2");
    await user.click(screen.getByRole("button", { name: "Save override" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Model ID is invalid");
    expect(screen.getByRole("textbox", { name: "Model ID" })).toHaveValue("bad-model");
  });
});
