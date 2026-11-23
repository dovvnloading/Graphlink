import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { OllamaSettingsPage } from "./OllamaSettingsPage";
import { formatLocalModelBytes } from "./LocalProviderSettings";

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
  llamaCppModels: [],
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
    // Health checks are untracked: only the published snapshot matters, not
    // a request acknowledgement that could time out after Connected.
    intent: (topic: string, intent: string, args: unknown[] = []) => {
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
  await user.click(within(screen.getByRole("listbox", { name: field })).getByRole("option", { name: option }));
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
    expect(screen.getByRole("status", { name: "Ollama server status" })).toHaveTextContent("Installed, not running");
    expect(screen.getByText("llama:latest")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Low" }));
    expect(intents).toContainEqual(["app-settings", "setOllamaReasoningLevel", ["low"]]);
    await user.click(screen.getByRole("button", { name: "Start Ollama" }));
    expect(intents).toContainEqual(["app-settings", "startOllama", []]);
    update({ ollamaStartStatus: "starting" });
    expect(screen.getByRole("button", { name: "Start Ollama" })).toBeDisabled();
    expect(screen.getByText(/Starting Ollama and waiting/)).toHaveAttribute("role", "status");
    update({ ollamaServerStatus: "running", ollamaServerReachable: true, ollamaStartStatus: "idle" });
    expect(screen.queryByRole("button", { name: "Start Ollama" })).toBeNull();
    expect(screen.getByRole("status", { name: "Ollama server status" })).toHaveTextContent("Running");
    await user.click(screen.getByRole("button", { name: "Check Ollama status" }));
    expect(intents).toContainEqual(["app-settings", "checkOllamaStatus", []]);
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
    const inventory = within(screen.getByRole("region", { name: "Local model inventory" }));
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
    expect(screen.getByRole("group", { name: "Capabilities for disk:latest" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check disk:latest" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Remove disk:latest" })).toBeNull();
    expect(screen.getByRole("button", { name: "Check disk:latest" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Context window for disk:latest" }));
    expect(screen.getByRole("option", { name: "8k" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "16k" })).toBeNull();
  });

  it("each card owns its context window and applies the verified trained limit", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true, ollamaServerStatus: "running",
      ollamaInventory: [
        inventoryModel("small:latest", { trainedContextLength: 16384 }),
        inventoryModel("large:latest", { trainedContextLength: 131072 }),
      ],
    });
    expect(screen.getByRole("button", { name: "Context window for small:latest" })).toHaveTextContent("8k");
    expect(screen.getByRole("button", { name: "Context window for large:latest" })).toHaveTextContent("8k");
    await user.click(screen.getByRole("button", { name: "Context window for small:latest" }));
    expect(screen.getByRole("option", { name: "16k" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "32k" })).toBeNull();
    await user.click(screen.getByRole("option", { name: "16k" }));
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
    await chooseOption(user, "Chat Model", "Auto — choose a compatible installed model");
    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_chat", "auto"]]);
    await user.click(screen.getByRole("button", { name: "Chat Model" }));
    expect(screen.queryByRole("option", { name: "Use chat model" })).toBeNull();
  });

  it("does not present folder-discovered or datalist-only names as installed choices", async () => {
    const { user } = setup({
      ollamaInventorySource: "folder",
      ollamaInventory: [inventoryModel("disk:latest", { source: "folder" })],
      ollamaScannedModels: ["disk:latest", "only-in-datalist:latest"],
    });
    await user.click(screen.getByRole("button", { name: "Chat Model" }));
    expect(screen.queryByRole("option", { name: "disk:latest" })).toBeNull();
    expect(screen.queryByRole("option", { name: "only-in-datalist:latest" })).toBeNull();
    expect(screen.getByRole("option", { name: "Custom model ID…" })).toBeInTheDocument();
  });

  it("preserves unknown IDs via the custom option, with multi-character typing even without server echoes", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: [inventoryModel("alpha:latest")],
      ollamaModelAssignments: { task_chart: "private:tag" },
    });
    expect(screen.getByRole("combobox", { name: "Chart Generation Model (custom model ID)" }))
      .toHaveValue("private:tag");
    await chooseOption(user, "Chat Model", "Custom model ID…");
    const input = screen.getByRole("combobox", { name: "Chat Model (custom model ID)" });
    await user.type(input, "my-model:latest");
    expect(input).toHaveValue("my-model:latest");
    expect(intents).not.toContainEqual([
      "app-settings", "setOllamaModelAssignment", ["task_chat", "my-model:latest"],
    ]);
    await user.click(screen.getByRole("button", { name: "Save Chat Model ID" }));
    expect(intents.filter(([, action, args]) => action === "setOllamaModelAssignment" && args[0] === "task_chat"))
      .toEqual([["app-settings", "setOllamaModelAssignment", ["task_chat", "my-model:latest"]]]);
  });

  it("selects installed models with keyboard arrows/Enter and closes with Escape without losing focus", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: [inventoryModel("alpha:latest"), inventoryModel("beta:latest")],
    });
    const trigger = screen.getByRole("button", { name: "Chat Naming Model" });
    trigger.focus();
    await user.keyboard("{Enter}");
    const listbox = screen.getByRole("listbox", { name: "Chat Naming Model" });
    expect(listbox).toHaveFocus();
    expect(listbox).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Auto — choose a compatible installed model" }).id);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_title", "alpha:latest"]]);
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}{Escape}");
    expect(screen.queryByRole("listbox", { name: "Chat Naming Model" })).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("checks one model without changing assignments and cancels only the running check", async () => {
    const { user, intents, update } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true, ollamaServerStatus: "running",
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
    expect(card.getByText(/Sample load time: 2.25 s/)).toHaveTextContent("42.5 tokens/s");
    expect(card.getByText(/Tool probe: No tool call observed/)).toHaveAttribute("data-level", "warning");
    expect(card.getByText(/Tool probe not supported/)).toBeInTheDocument();
    update({ ollamaModelChecks: [{
      name: "first:latest", status: "complete", loadSeconds: 2.25,
      tokensPerSecond: 42.5, toolCallingWorks: null,
      message: "Tool probe inconclusive: reasoning output without a matching tool call.",
    }] });
    expect(card.getByText(/Tool probe: Inconclusive/)).toHaveAttribute("data-level", "warning");
    expect(card.getByText(/Tool probe inconclusive: reasoning output/)).toBeInTheDocument();
    update({ ollamaModelChecks: [{
      name: "first:latest", status: "error", loadSeconds: null,
      tokensPerSecond: null, toolCallingWorks: null, message: "Check failed: daemon offline",
    }] });
    expect(card.getByRole("alert")).toHaveTextContent("Check failed: daemon offline");
  });

  it("keeps removal confirmation inside the card, supports Escape/Keep, and requires explicit confirmation", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true,
      ollamaInventory: [inventoryModel("gemma3:4b")],
    });
    const remove = screen.getByRole("button", { name: "Remove gemma3:4b" });
    await user.click(remove);
    expect(screen.getByRole("button", { name: "Confirm remove gemma3:4b" })).toHaveFocus();
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
    const { user, intents } = setup({ ollamaServerStatus: "running", ollamaServerReachable: true });
    const input = screen.getByRole("combobox", { name: "Model ID to download" });
    const pull = screen.getByRole("button", { name: "Download model" });
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
    const { update } = setup({ ollamaServerStatus: "running", ollamaServerReachable: true });
    update({
      ollamaPullStatus: "finalizing", ollamaPullModel: "gemma3:4b",
      ollamaPullMessage: "Verifying installed model...",
    });
    expect(screen.getByText(/Verifying installed model/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel download" })).toBeNull();
    expect(screen.getByRole("button", { name: "Pull qwen3:4b" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refresh installed models" })).toBeDisabled();
  });

  it("scans and refreshes, supports manual pull, and cancels running downloads", async () => {
    const { user, intents, update } = setup({ ollamaServerStatus: "running", ollamaServerReachable: true });
    await user.click(screen.getByRole("button", { name: "Refresh installed models" }));
    expect(intents.filter(([, action]) => action === "scanOllamaSystem")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Pull qwen3:4b" })).toBeEnabled();
    await user.type(screen.getByRole("combobox", { name: "Model ID to download" }), "custom:tag");
    await user.click(screen.getByRole("button", { name: "Download model" }));
    expect(intents).toContainEqual(["app-settings", "pullOllamaModel", ["custom:tag"]]);
    update({
      ollamaPullStatus: "running", ollamaPullModel: "custom:tag",
      ollamaPullCompleted: 500, ollamaPullTotal: 1000, ollamaPullMessage: "downloading",
    });
    expect(screen.getByRole("progressbar", { name: "Download progress for custom:tag" }))
      .toHaveAttribute("aria-valuenow", "500");
    await user.click(screen.getByRole("button", { name: "Cancel download" }));
    expect(intents).toContainEqual(["app-settings", "cancelOllamaPull", []]);
    expect(screen.getByRole("button", { name: "Refresh installed models" })).toBeDisabled();
  });

  it("shows installed recommendations, offline notice and errors without attempting model changes", async () => {
    const { user, intents, update } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true, ollamaServerStatus: "running",
      ollamaInventory: [inventoryModel("qwen3:4b")],
    });
    expect(screen.getByRole("button", { name: "Pull qwen3:4b" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pull gemma3:4b" })).toBeEnabled();
    update({ ollamaInventorySource: "folder", ollamaServerReachable: false,
      ollamaServerStatus: "installed", ollamaNotice: "Daemon offline." });
    expect(screen.getByText(/Ollama is not responding/)).toHaveAttribute("role", "status");
    expect(screen.getByText("Daemon offline.")).toHaveAttribute("role", "alert");
    expect(screen.getByRole("button", { name: "Pull gemma3:4b" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Scan model folder…" }));
    expect(intents).toContainEqual(["app-settings", "pickOllamaScanFolder", []]);
  });
  it("keeps the shared model byte labels conservative for untrusted values", () => {
    expect(formatLocalModelBytes(null)).toBe("Unknown");
    expect(formatLocalModelBytes(Number.NaN)).toBe("Unknown");
    expect(formatLocalModelBytes(Number.POSITIVE_INFINITY)).toBe("Unknown");
    expect(formatLocalModelBytes(-10)).toBe("Unknown");
    expect(formatLocalModelBytes(0)).toBe("0 B");
    expect(formatLocalModelBytes(1024)).toBe("1.0 KiB");
  });



  it("explains first-time installation and offers keyboard-operable recovery without attempting downloads", async () => {
    const { user, intents, update } = setup({
      ollamaServerStatus: "not_found", ollamaServerReachable: false,
    });
    expect(screen.getByRole("status", { name: "Ollama server status" })).toHaveTextContent("Not installed");
    expect(screen.getByRole("link", { name: /Install Ollama/ }))
      .toHaveAttribute("href", "https://ollama.com/download");
    expect(screen.queryByRole("button", { name: "Start Ollama" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pull qwen3:4b" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download model" })).toBeDisabled();
    expect(intents.some(([, intent]) => intent === "pullOllamaModel")).toBe(false);

    const check = screen.getByRole("button", { name: "Check Ollama status" });
    check.focus();
    await user.keyboard("{Enter}");
    expect(intents).toContainEqual(["app-settings", "checkOllamaStatus", []]);
    update({ ollamaStartError: "Graphlink could not find an Ollama installation." });
    expect(screen.getByRole("alert")).toHaveTextContent("Graphlink could not find");
    update({ ollamaServerStatus: "installed", ollamaStartError: "" });
    expect(screen.getByRole("button", { name: "Start Ollama" })).toBeEnabled();
  });

  it("reveals selected values and traverses the portaled task picker with a single Tab", async () => {
    const { user } = setup();
    const field = screen.getByRole("button", { name: "Chat Model" });
    expect(field).toHaveTextContent("Auto — choose a compatible installed model");
    expect(field).not.toHaveAccessibleDescription(/Selected:/);
    field.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("option", { name: "Auto — choose a compatible installed model" }))
      .toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Tab}");
    expect(screen.queryByRole("listbox", { name: "Chat Model" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Chat Naming Model" })).toHaveFocus();
  });

  it("filters known embedding-only models from text tasks and warns on an existing incompatible assignment", async () => {
    const { user, update } = setup({
      ollamaInventorySource: "server",
      ollamaInventory: [
        inventoryModel("nomic-embed-text:latest", { capabilities: ["embeddings"] }),
        inventoryModel("chat:latest", { capabilities: ["chat", "tools"] }),
        inventoryModel("unverified:latest", { capabilities: [] }),
      ],
      ollamaModelAssignments: { task_chat: "nomic-embed-text:latest" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/embedding-only model and cannot generate text/);
    await user.click(screen.getByRole("button", { name: "Chat Model" }));
    expect(screen.queryByRole("option", { name: "nomic-embed-text:latest" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "chat:latest" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "unverified:latest" })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "chat:latest" }));
    update({ ollamaModelAssignments: { task_chat: "chat:latest" } });
    expect(screen.queryByText(/embedding-only model and cannot generate text/)).not.toBeInTheDocument();
  });

  it("hides chat context controls and preset errors on embedding-only model cards", () => {
    const { intents } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true, ollamaServerStatus: "running",
      ollamaInventory: [
        inventoryModel("nomic-embed-text:latest", {
          capabilities: ["embeddings"], trainedContextLength: 6000, sizeBytes: 274_000_000,
        }),
        inventoryModel("chat:latest", { trainedContextLength: 16384 }),
      ],
      ollamaContextWindows: { "nomic-embed-text:latest": 8192 },
      ollamaContextModel: "nomic-embed-text:latest", ollamaContextStatus: "ready",
      ollamaContextModelSizeBytes: 274_000_000,
      ollamaContextKvBytesPerToken: 131072,
      ollamaContextAvailableMemoryBytes: 128 * 1024 ** 3,
    });
    const embedding = within(screen.getByRole("group", { name: "nomic-embed-text:latest" }));
    const chat = within(screen.getByRole("group", { name: "chat:latest" }));
    expect(embedding.getByText(/261.3 MiB on disk/)).toBeInTheDocument();
    expect(embedding.queryByRole("button", { name: /Context window/ })).toBeNull();
    expect(embedding.queryByText(/supported presets/)).toBeNull();
    expect(embedding.queryByText(/Estimated model weights/)).toBeNull();
    expect(embedding.queryByText(/Context options above 8k/)).toBeNull();
    expect(chat.getByRole("button", { name: "Context window for chat:latest" })).toBeInTheDocument();
    expect(intents).not.toContainEqual(["app-settings", "inspectOllamaContextModel", ["nomic-embed-text:latest"]]);
  });

  it("shows freshly loaded state and resident memory after a Check report arrives", () => {
    const model = inventoryModel("gemma4:12b", { sizeBytes: 7_400_000_000, loaded: false });
    const { update } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true,
      ollamaInventory: [model],
    });
    const card = within(screen.getByRole("group", { name: "gemma4:12b" }));
    expect(card.getByText("Not loaded")).toBeInTheDocument();
    expect(card.getByText(/6.9 GiB on disk/)).toBeInTheDocument();
    update({ ollamaInventory: [{ ...model, loaded: true,
      memoryBytes: 8_000_000_000, vramBytes: 6_000_000_000 }],
    });
    expect(card.queryByText("Not loaded")).toBeNull();
    expect(card.getByText("Loaded")).toBeInTheDocument();
    expect(card.getByText(/7.5 GiB memory in use/)).toHaveTextContent("5.6 GiB VRAM");
  });

  it("re-inspects the context estimate when a saved context window changes", () => {
    const { intents, update } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true, ollamaServerStatus: "running",
      ollamaInventory: [inventoryModel("gemma4:12b", { sizeBytes: 7_400_000_000 })],
      ollamaCurrentModel: "gemma4:12b",
    });
    const inspections = () => intents.filter(([, name]) => name === "inspectOllamaContextModel");
    expect(inspections()).toHaveLength(1);
    update({ ollamaContextWindows: { "gemma4:12b": 16384 } });
    expect(inspections()).toHaveLength(2);
    expect(inspections()[1]).toEqual(["app-settings", "inspectOllamaContextModel", ["gemma4:12b"]]);
  });

  it("does not misrepresent a saved context larger than a model's trained limit", async () => {
    const { user, intents } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true, ollamaServerStatus: "running",
      ollamaInventory: [inventoryModel("six-k:latest", { trainedContextLength: 6000 })],
      ollamaContextWindows: { "six-k:latest": 8192 },
    });
    const card = within(screen.getByRole("group", { name: "six-k:latest" }));
    expect(card.getByRole("button", { name: "Context window for six-k:latest" })).toHaveTextContent("4k");
    expect(card.getByRole("status")).toHaveTextContent(/saved 8,192-token window.*supported presets/);
    await user.click(card.getByRole("button", { name: "Context window for six-k:latest" }));
    expect(screen.queryByRole("option", { name: "8k" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "4k" }));
    expect(intents).toContainEqual(["app-settings", "setOllamaContextWindow", ["six-k:latest", 4096]]);
  });

  it("saves custom model IDs only on explicit confirmation or Enter, not for each keystroke", async () => {
    const { user, intents } = setup();
    await chooseOption(user, "Chat Model", "Custom model ID…");
    const input = screen.getByRole("combobox", { name: "Chat Model (custom model ID)" });
    await user.type(input, "local:7b");
    expect(intents.some(([, action]) => action === "setOllamaModelAssignment")).toBe(false);
    await user.keyboard("{Enter}");
    expect(intents.filter(([, action]) => action === "setOllamaModelAssignment"))
      .toEqual([["app-settings", "setOllamaModelAssignment", ["task_chat", "local:7b"]]]);
    expect(screen.getByRole("button", { name: "Save Chat Model ID" })).toBeEnabled();
  });

  it("announces downloading, cancellation and completion without implying a model was selected", () => {
    const { update, intents } = setup({ ollamaServerStatus: "running", ollamaServerReachable: true });
    update({ ollamaPullStatus: "running", ollamaPullModel: "qwen3:4b",
      ollamaPullMessage: "downloading", ollamaPullCompleted: 2, ollamaPullTotal: 10 });
    expect(screen.getAllByRole("progressbar", { name: "Download progress for qwen3:4b" }))
      .toEqual(expect.arrayContaining([expect.objectContaining({})]));
    for (const progress of screen.getAllByRole("progressbar", { name: "Download progress for qwen3:4b" })) {
      expect(progress).toHaveAttribute("aria-valuenow", "2");
    }
    update({ ollamaPullStatus: "cancelled", ollamaPullModel: "qwen3:4b" });
    expect(screen.getByText("Download cancelled.")).toHaveAttribute("role", "status");
    update({ ollamaPullStatus: "done", ollamaPullModel: "qwen3:4b" });
    expect(screen.getByText("Downloaded qwen3:4b.")).toHaveAttribute("role", "status");
    expect(intents.some(([, action]) => action === "setOllamaModelAssignment")).toBe(false);
  });

  it("moves focus to removal feedback after the removed card disappears", async () => {
    const { user, update } = setup({
      ollamaInventorySource: "server", ollamaServerReachable: true,
      ollamaInventory: [inventoryModel("old:latest")],
    });
    await user.click(screen.getByRole("button", { name: "Remove old:latest" }));
    await user.click(screen.getByRole("button", { name: "Confirm remove old:latest" }));
    update({ ollamaInventory: [], ollamaRemoveStatus: "done" });
    const message = screen.getByText("Removed old:latest from Ollama.");
    expect(message).toHaveAttribute("role", "status");
    expect(message).toHaveFocus();
  });
});
