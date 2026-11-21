import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Dialog, OverlayProvider, useOverlays } from "../overlays/overlays";
import { initialComposerState, type ComposerStore, type PickerModel } from "./composerStore";
import { ModelPicker } from "./ModelPicker";

function makeStore(models: PickerModel[]) {
  const loadPickerModels = vi.fn().mockResolvedValue(models);
  const selectPickerModel = vi.fn();
  const openOllamaSettings = vi.fn();
  const composer = { ...initialComposerState, route: {
    ...initialComposerState.route, mode: "ollama" as const, modelId: "",
  } };
  const store = {
    subscribe: () => () => {},
    getComposer: () => composer,
    loadPickerModels,
    selectPickerModel,
    openOllamaSettings,
  } as unknown as ComposerStore;
  return { store, loadPickerModels, selectPickerModel, openOllamaSettings };
}

function Host({ store }: { store: ComposerStore }) {
  const overlays = useOverlays();
  return (
    <>
      <button data-overlay-trigger="model" onClick={() => overlays.toggle("model", "popover")}>
        Choose a model
      </button>
      {overlays.isOpen("model") && <ModelPicker store={store} />}
      <Dialog name="settings" title="Settings"><p>Ollama setup</p></Dialog>
    </>
  );
}

function mount(store: ComposerStore) {
  return render(<OverlayProvider><Host store={store} /></OverlayProvider>);
}

const local: PickerModel = {
  provider: "Ollama", modelId: "qwen3:8b", label: "Ollama · qwen3:8b",
  source: "local", capabilities: ["chat", "tools", "vision", "thinking"], sizeBytes: 2 * 1024 ** 3,
};
const embedding: PickerModel = {
  provider: "Ollama", modelId: "nomic-embed-text", label: "Ollama · nomic-embed-text",
  source: "local", capabilities: ["embeddings"], sizeBytes: 600 * 1024 ** 2,
};
const cloud: PickerModel = {
  provider: "Anthropic Claude", modelId: "claude-test", label: "Anthropic Claude · claude-test",
  source: "cloud", capabilities: [], sizeBytes: null,
};

describe("shared local/cloud model picker", () => {
  it("opens with zero models, navigates to Settings > Ollama, and restores trigger focus on Escape", async () => {
    const user = userEvent.setup();
    const { store, openOllamaSettings, loadPickerModels } = makeStore([]);
    mount(store);
    const trigger = screen.getByRole("button", { name: "Choose a model" });
    await user.click(trigger);
    expect(await screen.findByText("No models configured yet.")).toBeVisible();
    expect(loadPickerModels).toHaveBeenCalledOnce();
    expect(screen.getByRole("searchbox", { name: "Search models" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Choose a model" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.click(await screen.findByRole("button", { name: "Set up a local model" }));
    expect(openOllamaSettings).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeVisible();
  });

  it("shows installed capabilities and size, including embedding-only, beside configured cloud", async () => {
    const user = userEvent.setup();
    const { store } = makeStore([local, embedding, cloud]);
    mount(store);
    await user.click(screen.getByRole("button", { name: "Choose a model" }));
    const model = await screen.findByRole("button", { name: /Ollama · qwen3:8b/ });
    expect(within(model).getByText("tools")).toBeVisible();
    expect(within(model).getByText("vision")).toBeVisible();
    expect(within(model).getByText("thinking")).toBeVisible();
    expect(within(model).getByText("2.0 GiB")).toBeVisible();
    const embed = screen.getByRole("button", { name: /nomic-embed-text/ });
    expect(within(embed).getByText("embeddings")).toBeVisible();
    expect(embed).toBeDisabled();
    expect(screen.getByRole("button", { name: /Anthropic Claude · claude-test/ })).toBeEnabled();
  });

  it("supports search, ArrowDown, Enter, Tab and provider-qualified cloud selection", async () => {
    const user = userEvent.setup();
    const { store, selectPickerModel } = makeStore([local, cloud]);
    mount(store);
    await user.click(screen.getByRole("button", { name: "Choose a model" }));
    const search = screen.getByRole("searchbox", { name: "Search models" });
    await waitFor(() => expect(screen.getByRole("button", { name: /Anthropic Claude · claude-test/ })).toBeVisible());
    await user.type(search, "claude-test");
    await user.keyboard("{Enter}");
    expect(selectPickerModel).toHaveBeenCalledExactlyOnceWith("Anthropic Claude", "claude-test");

    await user.click(screen.getByRole("button", { name: "Choose a model" }));
    await screen.findByRole("button", { name: /Ollama · qwen3:8b/ });
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: /Ollama · qwen3:8b/ })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(selectPickerModel).toHaveBeenCalledWith("Ollama", "qwen3:8b");
  });

  it("keeps setup available when inventory discovery fails", async () => {
    const user = userEvent.setup();
    const { store, loadPickerModels } = makeStore([]);
    loadPickerModels.mockRejectedValue(new Error("daemon offline"));
    mount(store);
    await user.click(screen.getByRole("button", { name: "Choose a model" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load models.");
    expect(screen.getByRole("button", { name: "Set up a local model" })).toBeEnabled();
  });
});
