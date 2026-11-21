import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { OllamaSettingsPage } from "./OllamaSettingsPage";

// A standalone page fixture. Navigation/subscription wiring remains covered
// by SettingsDialog.test.tsx; this file exercises the actual Ollama controls.
const baseState: AppSettingsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  activeSection: "general",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: { info: true, success: true, warning: true, error: true },
  githubTokenConfigured: false,
  falApiKeySource: "none",
  falJobsJson: "[]",
  falNotice: "",
  falBusy: false,
  falSyncResultJson: "",
  falDiscoveryJson: "",
  falUploadUrl: "",
  secretsEncryptedAtRest: true,
  logLevel: "INFO",
  autoModelPolicy: "cheapest-capable",
  theme: "system",
  // ADR-012 stage 12.6: required by the app-settings-state contract - see
  // OnboardingDialog.test.tsx for the surface that actually exercises this.
  hasCompletedOnboarding: false,
  // ADR-012 stage 12.6: required by the app-settings-state contract
  // (SettingsDialog.tsx's own initialState default is "Ollama (Local)").
  providerMode: "Ollama (Local)",
  activeApiProvider: "OpenAI-Compatible",
  viewingApiProvider: "OpenAI-Compatible",
  apiBaseUrl: "https://api.openai.com/v1",
  apiKeyConfigured: { openai: false, anthropic: false, gemini: false },
  apiKeySource: { openai: "none", anthropic: "none", gemini: "none" },
  apiProviders: [
    { id: "OpenAI-Compatible", label: "Custom endpoint", group: "Custom", description: "OpenAI-compatible API",
      docsUrl: "", defaultBaseUrl: "https://api.openai.com/v1", editableEndpoint: true, endpointHint: "",
      supportsCatalog: true, supportsImageGeneration: true, keySlot: "openai", envVar: "OPENAI_API_KEY" },
    { id: "Anthropic Claude", label: "Anthropic", group: "Model labs", description: "Claude",
      docsUrl: "https://platform.claude.com/docs", defaultBaseUrl: "https://api.anthropic.com", editableEndpoint: false,
      endpointHint: "", supportsCatalog: true, supportsImageGeneration: false, keySlot: "anthropic", envVar: "ANTHROPIC_API_KEY" },
    { id: "Google Gemini", label: "Google Gemini", group: "Model labs", description: "Gemini",
      docsUrl: "", defaultBaseUrl: "", editableEndpoint: false, endpointHint: "", supportsCatalog: true,
      supportsImageGeneration: true, keySlot: "gemini", envVar: "GEMINI_API_KEY" },
    { id: "Azure OpenAI", label: "Azure OpenAI", group: "Cloud platforms", description: "Deployment names",
      docsUrl: "", defaultBaseUrl: "", editableEndpoint: true, endpointHint: "Use your resource endpoint.",
      supportsCatalog: false, supportsImageGeneration: false, keySlot: "azure", envVar: "" },
  ],
  apiModels: {},
  apiModelCatalog: [],
  apiCatalogBaseUrl: "",
  apiCatalogStatus: "idle",
  apiCatalogMessage: "Model catalog has not been refreshed yet.",
  geminiStaticModels: ["gemini-2.5-flash", "gemini-2.5-pro"],
  geminiStaticImageModels: ["gemini-2.5-flash-image"],
  ollamaReasoningLevel: "high",
  ollamaCurrentModel: "",
  ollamaModelAssignments: {},
  ollamaContextWindows: {},
  ollamaContextModel: "",
  ollamaContextStatus: "idle",
  ollamaContextNotice: "",
  ollamaContextTrainedLength: 0,
  ollamaContextModelSizeBytes: 0,
  ollamaContextKvBytesPerToken: 0,
  ollamaContextAvailableMemoryBytes: 0,
  ollamaScannedModels: [],
  ollamaInventory: [],
  ollamaModelChecks: [],
  ollamaInventorySource: "",
  ollamaServerReachable: null,
  ollamaServerStatus: "checking",
  ollamaStartStatus: "idle",
  ollamaStartError: "",
  ollamaScanSummary: "No saved scan yet. Run a system scan or choose a folder to build the local model list.",
  ollamaScanStatus: "idle",
  ollamaPullStatus: "idle",
  ollamaPullModel: "",
  ollamaPullMessage: "",
  ollamaPullCompleted: 0,
  ollamaPullTotal: 0,
  ollamaRemoveStatus: "idle",
  ollamaRemoveModel: "",
  ollamaNotice: "",
  knowledgeEmbeddingSource: "off",
  knowledgeEmbeddingModel: "",
  knowledgeCloudProvider: "OpenAI",
  knowledgeCloudOptIn: false,
  knowledgeOllamaModels: [],
  knowledgeOllamaCapableModels: [],
  knowledgeScanStatus: "idle",
  knowledgeTestStatus: "idle",
  knowledgeNotice: "",
  llamaCppReasoningLevel: "high",
  llamaCppChatModelPath: "",
  llamaCppTitleModelPath: "",
  llamaCppChatFormat: "",
  llamaCppNCtx: 4096,
  llamaCppNGpuLayers: 0,
  llamaCppNThreads: 0,
  llamaCppScannedModels: [],
  llamaCppScanSummary: "No saved GGUF scan yet. Run a system scan or choose a folder to build the local model list.",
  llamaCppScanStatus: "idle",
  llamaCppNotice: "",
  // ADR-012 stage 12.6: required by the app-settings-state contract.
  harnessTrustedDirs: [],
  mcpServers: [],
};

type InventoryModel = AppSettingsState["ollamaInventory"][number];
function inventoryModel(name: string, overrides: Partial<InventoryModel> = {}): InventoryModel {
  return {
    name, sizeBytes: null, family: "llama", parameterSize: "7B", quantization: "Q4_K_M",
    capabilities: ["chat"], trainedContextLength: 32768, loaded: false,
    memoryBytes: null, vramBytes: null, source: "server", ...overrides,
  };
}

function setup(overrides: Partial<AppSettingsState> = {}) {
  const intents: Array<[string, string, unknown[]]> = [];
  const transport = {
    fireIntent: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
    },
  } as unknown as WsTransport;
  const user = userEvent.setup();
  let state = { ...baseState, ...overrides };
  const view = render(<OllamaSettingsPage state={state} transport={transport} />);
  const update = (patch: Partial<AppSettingsState>) => {
    state = { ...state, ...patch };
    act(() => view.rerender(<OllamaSettingsPage state={state} transport={transport} />));
  };
  return { user, intents, update };
}

async function chooseOption(
  user: ReturnType<typeof userEvent.setup>, field: string, option: string,
) {
  await user.click(screen.getByRole("button", { name: field }));
  await user.click(screen.getByRole("button", { name: option }));
}

describe("Settings > Ollama", () => {
  it("uses the shared inventory and scans when the page opens, without pulling/removing anything", () => {
    const { intents } = setup();
    expect(intents).toContainEqual(["app-settings", "scanOllamaSystem", []]);
    expect(intents.some(([, action]) => action === "pullOllamaModel" || action === "removeOllamaModel")).toBe(false);
    expect(screen.getByText("Ollama (Local) is the active provider mode.")).toBeInTheDocument();
  });

  it("shows reasoning levels, current model, server states and the start action", async () => {
    const { user, intents, update } = setup({ ollamaCurrentModel: "llama:latest", ollamaServerStatus: "installed" });
    expect(screen.getByText("llama:latest")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Low"));
    expect(intents).toContainEqual(["app-settings", "setOllamaReasoningLevel", ["low"]]);
    await user.click(screen.getByRole("button", { name: "Start Ollama" }));
    expect(intents).toContainEqual(["app-settings", "startOllama", []]);
    update({ ollamaServerStatus: "running" });
    expect(screen.queryByRole("button", { name: "Start Ollama" })).toBeNull();
  });

  it("renders model cards with sizes, capability badges, trained context and loaded memory", () => {
    setup({
      ollamaInventorySource: "server", ollamaServerReachable: true,
      ollamaInventory: [inventoryModel("vision:latest", {
        sizeBytes: 4 * 1024 ** 3, family: "qwen", parameterSize: "7B",
        capabilities: ["chat", "tools", "vision", "thinking"],
        trainedContextLength: 131072, loaded: true,
        memoryBytes: 5 * 1024 ** 3, vramBytes: 3 * 1024 ** 3,
      })],
    });
    const inventory = within(screen.getByRole("group", { name: "Local model inventory" }));
    const card = within(inventory.getByRole("group", { name: "vision:latest" }));
    expect(card.getByText("vision:latest")).toBeInTheDocument();
    expect(card.getByText("Loaded")).toBeInTheDocument();
    expect(card.getByText(/4.0 GiB on disk/)).toBeInTheDocument();
    expect(card.getByText(/Trained context: 131,072 tokens/)).toBeInTheDocument();
    expect(card.getByText(/5.0 GiB memory in use/)).toHaveTextContent("3.0 GiB VRAM");
    for (const capability of ["chat", "tools", "vision", "thinking"]) {
      expect(card.getByText(capability)).toHaveClass("settings-ollama-capability");
    }
    expect(card.getByRole("button", { name: "Context window for vision:latest" })).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Check vision:latest" })).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Remove vision:latest" })).toBeInTheDocument();
  });

  it("keeps disk-only rows marked unverified, and prevents removal or oversized context", async () => {
    const { user } = setup({
      ollamaInventorySource: "folder", ollamaServerReachable: false,
      ollamaInventory: [inventoryModel("disk:latest", {
        source: "folder", capabilities: [], trainedContextLength: 131072, loaded: null,
      })],
    });
    expect(screen.getByText("Load state unavailable")).toBeInTheDocument();
    expect(screen.getByText(/Trained context: Unverified/)).toBeInTheDocument();
    expect(screen.getByText(/Disk scan only/)).toBeInTheDocument();
    expect(screen.getByText("Capabilities unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove disk:latest" })).toBeNull();
    expect(screen.getByRole("button", { name: "Check disk:latest" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Context window for disk:latest" }));
    expect(screen.getByRole("button", { name: "8k" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "16k" })).toBeNull();
  });

  it("each card owns its context window and applies the verified trained limit", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: [
        inventoryModel("small:latest", { trainedContextLength: 16384 }),
        inventoryModel("large:latest", { trainedContextLength: 131072 }),
      ],
    });
    expect(screen.getByRole("button", { name: "Context window for small:latest" })).toHaveTextContent("8k");
    expect(screen.getByRole("button", { name: "Context window for large:latest" })).toHaveTextContent("8k");
    await user.click(screen.getByRole("button", { name: "Context window for small:latest" }));
    expect(screen.getByRole("button", { name: "16k" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "32k" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "16k" }));
    expect(intents).toContainEqual(["app-settings", "setOllamaContextWindow", ["small:latest", 16384]]);
    await chooseOption(user, "Context window for large:latest", "32k");
    expect(intents).toContainEqual(["app-settings", "setOllamaContextWindow", ["large:latest", 32768]]);
    expect(intents).toContainEqual(["app-settings", "inspectOllamaContextModel", ["large:latest"]]);
  });

  it("uses per-model saved context values and shows estimates only for the inspected model", () => {
    setup({
      ollamaInventorySource: "server",
      ollamaInventory: [
        inventoryModel("small:latest"),
        inventoryModel("large:latest", { trainedContextLength: 131072 }),
      ],
      ollamaContextWindows: { "large:latest": 32768 },
      ollamaContextModel: "large:latest", ollamaContextStatus: "ready",
      ollamaContextModelSizeBytes: 4 * 1024 ** 3,
      ollamaContextKvBytesPerToken: 131072,
      ollamaContextAvailableMemoryBytes: 5 * 1024 ** 3,
    });
    const large = within(screen.getByRole("group", { name: "large:latest" }));
    const small = within(screen.getByRole("group", { name: "small:latest" }));
    expect(large.getByRole("button", { name: "Context window for large:latest" })).toHaveTextContent("32k");
    expect(large.getByText(/Estimated model weights \+ KV cache/)).toHaveTextContent("8.0 GiB");
    expect(large.getByRole("alert")).toHaveTextContent(/exceeds available system RAM/i);
    expect(small.queryByText(/Estimated model weights/)).toBeNull();
  });

  it("labels KV-only estimates with unknown weights as insufficient to assess memory fit", () => {
    setup({
      ollamaInventorySource: "server",
      ollamaInventory: [inventoryModel("unknown:7b", { sizeBytes: 0 })],
      ollamaContextModel: "unknown:7b", ollamaContextStatus: "ready",
      ollamaContextModelSizeBytes: 0,
      ollamaContextKvBytesPerToken: 131072,
      ollamaContextAvailableMemoryBytes: 64 * 1024 ** 3,
    });
    const card = within(screen.getByRole("group", { name: "unknown:7b" }));
    expect(card.getByText(/KV cache \(weights unavailable\)/)).toBeInTheDocument();
    expect(card.getByText(/cannot establish whether the model will fit/)).toBeInTheDocument();
  });

  it("does not warn about available memory when RAM is unavailable", () => {
    setup({
      ollamaInventory: [inventoryModel("model:7b")],
      ollamaContextModel: "model:7b", ollamaContextStatus: "ready",
      ollamaContextModelSizeBytes: 4 * 1024 ** 3,
      ollamaContextKvBytesPerToken: 131072,
      ollamaContextAvailableMemoryBytes: 0,
    });
    expect(screen.getByText(/Available system RAM:/)).toHaveTextContent("unavailable");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers every installed server model directly for every task without losing Auto or inherit", async () => {
    const modelNames = ["alpha:latest", "beta:latest"];
    const { user, intents } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: modelNames.map((name) => inventoryModel(name)),
    });
    const tasks = [
      ["Chat Model", "task_chat"],
      ["Chat Naming Model", "task_title"],
      ["Chart Generation Model", "task_chart"],
      ["Web Content Validation Model", "task_web_validate"],
      ["Web Content Summarization Model", "task_web_summarize"],
    ];
    for (const [label, task] of tasks) {
      await chooseOption(user, label, "beta:latest");
      expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", [task, "beta:latest"]]);
    }
    await chooseOption(user, "Chat Naming Model", "Use chat model");
    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_title", "inherit"]]);
    await chooseOption(user, "Chat Model", "Auto - choose a compatible installed model");
    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_chat", "auto"]]);
    await user.click(screen.getByRole("button", { name: "Chat Model" }));
    expect(screen.queryByRole("button", { name: "Use chat model" })).toBeNull();
  });

  it("does not present folder-discovered or datalist-only names as installed choices", async () => {
    const { user } = setup({
      ollamaInventorySource: "folder",
      ollamaInventory: [inventoryModel("disk:latest", { source: "folder" })],
      ollamaScannedModels: ["disk:latest", "only-in-datalist:latest"],
    });
    await user.click(screen.getByRole("button", { name: "Chat Model" }));
    expect(screen.queryByRole("button", { name: "disk:latest" })).toBeNull();
    expect(screen.queryByRole("button", { name: "only-in-datalist:latest" })).toBeNull();
    expect(screen.getByRole("button", { name: "Custom model ID..." })).toBeInTheDocument();
  });

  it("preserves unknown IDs via the custom option, with multi-character typing even without server echoes", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: [inventoryModel("alpha:latest")],
      ollamaModelAssignments: { task_chart: "private:tag" },
    });
    expect(screen.getByRole("combobox", { name: "Chart Generation Model (custom model ID)" }))
      .toHaveValue("private:tag");
    await chooseOption(user, "Chat Model", "Custom model ID...");
    const input = screen.getByRole("combobox", { name: "Chat Model (custom model ID)" });
    await user.type(input, "my-model:latest");
    expect(input).toHaveValue("my-model:latest");
    expect(intents).toContainEqual([
      "app-settings", "setOllamaModelAssignment", ["task_chat", "my-model:latest"],
    ]);
  });

  it("selects installed models with keyboard arrows/Enter and closes with Escape without losing focus", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: [inventoryModel("alpha:latest"), inventoryModel("beta:latest")],
    });
    const trigger = screen.getByRole("button", { name: "Chat Naming Model" });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Auto - choose a compatible installed model" })).toHaveFocus();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_title", "alpha:latest"]]);
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}{Escape}");
    expect(screen.queryByRole("dialog", { name: "Chat Naming Model" })).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("checks one model without changing assignments and cancels only the running check", async () => {
    const { user, intents, update } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: [inventoryModel("first:latest"), inventoryModel("second:latest")],
    });
    await user.click(screen.getByRole("button", { name: "Check first:latest" }));
    expect(intents).toContainEqual(["app-settings", "checkOllamaModel", ["first:latest"]]);
    expect(intents.some(([, action]) => action === "setOllamaModelAssignment")).toBe(false);
    update({ ollamaModelChecks: [{
      name: "first:latest", status: "running", loadSeconds: null,
      tokensPerSecond: null, toolCallingWorks: null, message: "",
    }] });
    expect(screen.getByRole("button", { name: "Check first:latest" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check second:latest" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel check first:latest" }));
    expect(intents).toContainEqual(["app-settings", "cancelOllamaModelCheck", ["first:latest"]]);
  });

  it("shows probe results, failed tool calling and errors on the corresponding card", () => {
    const { update } = setup({ ollamaInventory: [inventoryModel("first:latest")] });
    update({ ollamaModelChecks: [{
      name: "first:latest", status: "complete", loadSeconds: 2.25,
      tokensPerSecond: 42.5, toolCallingWorks: false, message: "Tool probe not supported.",
    }] });
    const card = within(screen.getByRole("group", { name: "first:latest" }));
    expect(card.getByText(/Load: 2.25 s/)).toHaveTextContent("42.5 tokens/s");
    expect(card.getByText(/Tool calling: Not working/)).toBeInTheDocument();
    expect(card.getByText("Tool probe not supported.")).toBeInTheDocument();
    update({ ollamaModelChecks: [{
      name: "first:latest", status: "error", loadSeconds: null,
      tokensPerSecond: null, toolCallingWorks: null, message: "Check failed: daemon offline",
    }] });
    expect(card.getByText("Check failed: daemon offline")).toHaveAttribute("data-level", "error");
  });

  it("keeps removal confirmation inside the card, supports Escape/Keep, and requires explicit confirmation", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true,
      ollamaInventory: [inventoryModel("gemma3:4b")],
    });
    const remove = screen.getByRole("button", { name: "Remove gemma3:4b" });
    await user.click(remove);
    expect(screen.getByRole("button", { name: "Confirm remove" })).toHaveFocus();
    expect(intents.some(([, action]) => action === "removeOllamaModel")).toBe(false);
    await user.keyboard("{Escape}");
    expect(remove).toHaveFocus();
    await user.click(remove);
    await user.click(screen.getByRole("button", { name: "Keep model" }));
    expect(remove).toHaveFocus();
    expect(intents.some(([, action]) => action === "removeOllamaModel")).toBe(false);
    await user.click(remove);
    await user.keyboard("{Enter}");
    expect(intents).toContainEqual(["app-settings", "removeOllamaModel", ["gemma3:4b"]]);
  });

  it("rejects path tricks and oversized custom model IDs before dispatch", async () => {
    const { user, intents } = setup();
    const input = screen.getByRole("combobox", { name: "Validate and Pull Model" });
    const pull = screen.getByRole("button", { name: "Validate and Pull Model" });
    await user.type(input, "../outside:latest");
    expect(pull).toBeDisabled();
    await user.clear(input);
    await user.type(input, "registry.ollama.ai/library/gemma3:latest");
    expect(pull).toBeEnabled();
    fireEvent.change(input, { target: { value: "x".repeat(256) } });
    expect(pull).toBeDisabled();
    expect(intents.some(([, intent]) => intent === "pullOllamaModel")).toBe(false);
  });

  it("keeps model operations disabled during post-download verification", () => {
    const { update } = setup();
    update({
      ollamaPullStatus: "finalizing", ollamaPullModel: "gemma3:4b",
      ollamaPullMessage: "Verifying installed model...",
    });
    expect(screen.getByText(/Verifying installed model/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel download" })).toBeNull();
    expect(screen.getByRole("button", { name: "Pull qwen3:4b" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "System Scan" })).toBeDisabled();
  });

  it("scans and refreshes, supports manual pull, and cancels running downloads", async () => {
    const { user, intents, update } = setup();
    await user.click(screen.getByRole("button", { name: "System Scan" }));
    await user.click(screen.getByRole("button", { name: "Refresh installed models" }));
    expect(intents.filter(([, action]) => action === "scanOllamaSystem")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Pull qwen3:4b" })).toBeEnabled();
    await user.type(screen.getByRole("combobox", { name: "Validate and Pull Model" }), "custom:tag");
    await user.click(screen.getByRole("button", { name: "Validate and Pull Model" }));
    expect(intents).toContainEqual(["app-settings", "pullOllamaModel", ["custom:tag"]]);
    update({
      ollamaPullStatus: "running", ollamaPullModel: "custom:tag",
      ollamaPullCompleted: 500, ollamaPullTotal: 1000, ollamaPullMessage: "downloading",
    });
    expect(screen.getByRole("progressbar", { name: "Download progress for custom:tag" }))
      .toHaveAttribute("value", "500");
    await user.click(screen.getByRole("button", { name: "Cancel download" }));
    expect(intents).toContainEqual(["app-settings", "cancelOllamaPull", []]);
    expect(screen.getByRole("button", { name: "System Scan" })).toBeDisabled();
  });

  it("shows installed recommendations, offline notice and errors without attempting model changes", async () => {
    const { user, intents, update } = setup({
      ollamaInventorySource: "server", ollamaInventory: [inventoryModel("qwen3:4b")],
    });
    expect(screen.getByRole("button", { name: "Pull qwen3:4b" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pull gemma3:4b" })).toBeEnabled();
    update({ ollamaInventorySource: "folder", ollamaServerReachable: false, ollamaNotice: "Daemon offline." });
    expect(screen.getByRole("alert")).toHaveTextContent("Ollama is unavailable");
    expect(screen.getByText("Daemon offline.")).toHaveAttribute("data-level", "error");
    await user.click(screen.getByRole("button", { name: "Scan Folder..." }));
    expect(intents).toContainEqual(["app-settings", "pickOllamaScanFolder", []]);
  });
});
