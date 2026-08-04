import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SettingsDialog } from "./SettingsDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

// R7.4a: the first dedicated test file for SettingsDialog.tsx - recon for
// this increment confirmed every sibling chrome dialog built in the same
// R2.5/R2.6 batch had one except this file, a real gap rather than a
// documented decision. Focuses on the new API-provider page; General/
// Integrations are exercised indirectly through the same render/subscribe
// path and are lower-risk (no async network-shaped intents, no secrets).

const snapshot = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  activeSection: "general",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: { info: true, success: true, warning: true, error: true },
  githubTokenConfigured: false,
  secretsEncryptedAtRest: true,
  activeApiProvider: "OpenAI-Compatible",
  viewingApiProvider: "OpenAI-Compatible",
  apiBaseUrl: "https://api.openai.com/v1",
  apiKeyConfigured: { openai: false, anthropic: false, gemini: false },
  apiKeySource: { openai: "none", anthropic: "none", gemini: "none" },
  apiModels: {},
  apiModelCatalog: [],
  apiCatalogStatus: "idle",
  apiCatalogMessage: "Model catalog has not been refreshed yet.",
  geminiStaticModels: ["gemini-2.5-flash", "gemini-2.5-pro"],
  geminiStaticImageModels: ["gemini-2.5-flash-image"],
  ollamaReasoningLevel: "high",
  ollamaCurrentModel: "",
  ollamaModelAssignments: {},
  ollamaScannedModels: [],
  ollamaScanSummary: "No saved scan yet. Run a system scan or choose a folder to build the local model list.",
  ollamaScanStatus: "idle",
  ollamaPullStatus: "idle",
  ollamaNotice: "",
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

function OpenSettingsButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("settings", "dialog")}>
      open settings
    </button>
  );
}

function setup(initial: Record<string, unknown> = snapshot) {
  const user = userEvent.setup();
  const fake = makeTransport();
  render(
    <OverlayProvider>
      <OpenSettingsButton />
      <SettingsDialog transport={fake.transport} />
    </OverlayProvider>,
  );
  act(() => fake.push(initial));
  return { user, ...fake };
}

async function goToApiEndpoint(
  user: ReturnType<typeof userEvent.setup>,
  push: (payload: Record<string, unknown>) => void,
  overrides: Record<string, unknown> = {},
) {
  await user.click(screen.getByText("open settings"));
  await user.click(screen.getByRole("button", { name: "API Endpoint" }));
  // setActiveSection is fire-and-forget over the fake transport - nothing
  // echoes the new activeSection back automatically the way the real
  // backend's republish-on-mutation does, so the round trip is simulated
  // explicitly here (matching every other section's real activeSection
  // being server, not client, state).
  act(() => push({ ...snapshot, ...overrides, activeSection: "api endpoint" }));
}

async function goToOllama(
  user: ReturnType<typeof userEvent.setup>,
  push: (payload: Record<string, unknown>) => void,
  overrides: Record<string, unknown> = {},
) {
  await user.click(screen.getByText("open settings"));
  await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));
  act(() => push({ ...snapshot, ...overrides, activeSection: "ollama (local)" }));
}

async function goToLlamaCpp(
  user: ReturnType<typeof userEvent.setup>,
  push: (payload: Record<string, unknown>) => void,
  overrides: Record<string, unknown> = {},
) {
  await user.click(screen.getByText("open settings"));
  await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));
  act(() => push({ ...snapshot, ...overrides, activeSection: "llama.cpp (local)" }));
}

// Settings' selects are CustomSelect.tsx (chrome/CustomSelect.tsx), not
// native <select> - opening one and picking an option is a click on the
// trigger button (named via aria-label, matching the field's own visible
// label) followed by a click on the option button that appears (findBy,
// not getBy: the option panel portals to document.body asynchronously
// after the trigger click).
async function chooseCustomOption(
  user: ReturnType<typeof userEvent.setup>,
  triggerName: string,
  optionName: string,
) {
  await user.click(screen.getByRole("button", { name: triggerName }));
  await user.click(await screen.findByRole("button", { name: optionName }));
}

describe("SettingsDialog", () => {
  it("navigating sections fires setActiveSection with the clicked section's key", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open settings"));

    await user.click(screen.getByRole("button", { name: "Integrations" }));

    expect(intents).toContainEqual(["app-settings", "setActiveSection", ["integrations"]]);
  });

  it("API Endpoint page renders for the real (not deferred-placeholder) section", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push);

    expect(screen.queryByText(/lands in R7\.4b\/R7\.4c/)).toBeNull();
    expect(screen.getByText("API Provider")).toBeInTheDocument();
  });

  it("Llama.cpp page renders for the real (not deferred-placeholder) section", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push);

    expect(screen.queryByText(/lands in R7\.4c/)).toBeNull();
    expect(screen.getByText("Reasoning Level")).toBeInTheDocument();
  });

  it("Ollama page renders for the real (not deferred-placeholder) section", async () => {
    const { user, push } = setup();
    await goToOllama(user, push);

    expect(screen.queryByText(/lands in R7\.4c/)).toBeNull();
    expect(screen.getByText("Reasoning Level")).toBeInTheDocument();
  });

  it("clicking a reasoning level radio fires setOllamaReasoningLevel", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await user.click(screen.getByLabelText("Low"));

    expect(intents).toContainEqual(["app-settings", "setOllamaReasoningLevel", ["low"]]);
  });

  it("shows the current active model when one is set", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { ollamaCurrentModel: "llama3.2:3b" });

    expect(screen.getByText("llama3.2:3b")).toBeInTheDocument();
  });

  it("falls back to an Auto message when no current model is set", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { ollamaCurrentModel: "" });

    expect(screen.getByText("Auto - no compatible installed model found")).toBeInTheDocument();
  });

  it("System Scan fires scanOllamaSystem and disables while running", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push, { ollamaScanStatus: "running" });

    expect(screen.getByText("Scanning...")).toBeDisabled();

    act(() => push({ ...snapshot, activeSection: "ollama (local)", ollamaScanStatus: "idle" }));
    await user.click(screen.getByText("System Scan"));

    expect(intents).toContainEqual(["app-settings", "scanOllamaSystem", []]);
  });

  it("a per-task select defaults to auto and switching to inherit fires setOllamaModelAssignment immediately", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await chooseCustomOption(user, "Chat Naming Model", "Use chat model");

    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_title", "inherit"]]);
  });

  it("task_chat has no 'Use chat model' inherit option (it IS the chat model)", async () => {
    const { user, push } = setup();
    await goToOllama(user, push);

    await user.click(screen.getByRole("button", { name: "Chat Model" }));
    expect(screen.queryByRole("button", { name: "Use chat model" })).toBeNull();
  });

  it("switching a task to explicit reveals a text field; typing fires setOllamaModelAssignment", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await chooseCustomOption(user, "Chart Generation Model", "Custom model ID...");
    await user.type(screen.getByLabelText("Chart Generation Model (custom model ID)"), "x");

    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_chart", "x"]]);
  });

  it("an existing explicit assignment shows the custom-ID field pre-populated, not the special modes", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { ollamaModelAssignments: { task_chart: "qwen3:8b" } });

    expect(screen.getByLabelText("Chart Generation Model (custom model ID)")).toHaveValue("qwen3:8b");
  });

  it("scanned models populate the shared datalist used by task fields and the pull input", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { ollamaScannedModels: ["llama3.2:3b", "qwen3:8b"] });

    const datalist = document.getElementById("settings-ollama-scanned-models");
    expect(datalist?.querySelectorAll("option")).toHaveLength(2);
  });

  it("Validate and Pull Model stays disabled until a model name is typed", async () => {
    const { user, push } = setup();
    await goToOllama(user, push);

    const pullButton = screen.getByRole("button", { name: "Validate and Pull Model" });
    expect(pullButton).toBeDisabled();

    await user.type(screen.getByPlaceholderText("Advanced model ID entry"), "llama3.2:3b");
    expect(pullButton).toBeEnabled();
  });

  it("Validate and Pull Model fires pullOllamaModel with the typed name", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await user.type(screen.getByPlaceholderText("Advanced model ID entry"), "llama3.2:3b");
    await user.click(screen.getByRole("button", { name: "Validate and Pull Model" }));

    expect(intents).toContainEqual(["app-settings", "pullOllamaModel", ["llama3.2:3b"]]);
  });

  it("Validate and Pull Model is disabled and relabeled while a pull is running", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { ollamaPullStatus: "running" });

    await user.type(screen.getByPlaceholderText("Advanced model ID entry"), "llama3.2:3b");

    expect(screen.getByRole("button", { name: "Validating..." })).toBeDisabled();
  });

  it("an ollamaNotice renders as an inline error", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { ollamaNotice: "Model 'bogus' was not found on the Ollama hub." });

    expect(screen.getByText("Model 'bogus' was not found on the Ollama hub.")).toBeInTheDocument();
  });

  it("the API key field always starts blank, even when a key is already configured", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push, { apiKeyConfigured: { openai: true, anthropic: false, gemini: false } });

    const keyField = screen.getByPlaceholderText("A key is configured - enter a new one to replace it");
    expect(keyField).toHaveValue("");
  });

  it("shows a persistent unencrypted-secrets warning when DPAPI is unavailable, regardless of section", async () => {
    const { user, push } = setup();
    await user.click(screen.getByText("open settings"));
    act(() => push({ ...snapshot, secretsEncryptedAtRest: false }));

    // getByRole("alert") - not just getByText - pins the adversarial-review
    // fix that gave this banner role="alert" so screen readers actually
    // encounter it (the dialog's focus trap otherwise skips straight past
    // a non-focusable <p> to the rail buttons).
    expect(screen.getByRole("alert")).toHaveTextContent(/API keys are stored unencrypted on this system/);

    await user.click(screen.getByRole("button", { name: "Integrations" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/API keys are stored unencrypted on this system/);
  });

  it("hides the unencrypted-secrets warning when secrets are encrypted at rest", async () => {
    const { user, push } = setup();
    await user.click(screen.getByText("open settings"));
    act(() => push({ ...snapshot, secretsEncryptedAtRest: true }));

    expect(screen.queryByText(/API keys are stored unencrypted on this system/)).toBeNull();
  });

  it("shows an environment-variable hint next to the API Key field when that provider's key comes from the environment", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push, {
      apiKeySource: { openai: "environment", anthropic: "none", gemini: "none" },
    });

    expect(
      screen.getByText("Key provided by an environment variable, not Settings."),
    ).toBeInTheDocument();
  });

  it("hides the environment-variable hint when the viewed provider's key is stored or absent", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push, {
      apiKeySource: { openai: "stored", anthropic: "none", gemini: "none" },
    });

    expect(
      screen.queryByText("Key provided by an environment variable, not Settings."),
    ).toBeNull();
  });

  it("switching the provider select fires setViewingApiProvider", async () => {
    const { user, push, intents } = setup();
    await goToApiEndpoint(user, push);

    await chooseCustomOption(user, "API Provider", "Anthropic Claude");

    expect(intents).toContainEqual(["app-settings", "setViewingApiProvider", ["Anthropic Claude"]]);
  });

  it("Load Available Models is hidden for Gemini (no live catalog endpoint)", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push, { viewingApiProvider: "Google Gemini" });

    expect(screen.queryByText("Load Available Models")).toBeNull();
  });

  it("Save Configuration stays disabled until the key and every required model are filled in", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push);

    const saveButton = screen.getByText("Save Configuration");
    expect(saveButton).toBeDisabled();

    await user.type(screen.getByPlaceholderText("Enter your API key..."), "sk-test");
    expect(saveButton).toBeDisabled(); // still missing every per-task model

    for (const label of [
      "Chat Naming / Session Title",
      "Chat, Explain, Takeaways (main model)",
      "Chart Generation (code-capable model)",
      "Image Generation",
      "Web Content Validation",
      "Web Content Summarization",
    ]) {
      await user.type(screen.getByLabelText(label), "gpt-4o-mini");
    }

    expect(saveButton).toBeEnabled();
  });

  it("Anthropic does not require an Image Generation model to enable Save", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push, { viewingApiProvider: "Anthropic Claude", activeApiProvider: "Anthropic Claude" });

    await user.type(screen.getByPlaceholderText("Enter your API key..."), "sk-ant-test");
    expect(screen.queryByLabelText("Image Generation")).toBeNull();

    for (const label of [
      "Chat Naming / Session Title",
      "Chat, Explain, Takeaways (main model)",
      "Chart Generation (code-capable model)",
      "Web Content Validation",
      "Web Content Summarization",
    ]) {
      await user.type(screen.getByLabelText(label), "claude-sonnet");
    }

    expect(screen.getByText("Save Configuration")).toBeEnabled();
  });

  it("Save fires saveApiConfiguration with the typed draft and clears the key field afterward", async () => {
    const { user, push, intents } = setup();
    await goToApiEndpoint(user, push);

    await user.type(screen.getByPlaceholderText("Enter your API key..."), "sk-super-secret");
    for (const label of [
      "Chat Naming / Session Title",
      "Chat, Explain, Takeaways (main model)",
      "Chart Generation (code-capable model)",
      "Image Generation",
      "Web Content Validation",
      "Web Content Summarization",
    ]) {
      await user.type(screen.getByLabelText(label), "gpt-4o-mini");
    }
    await user.click(screen.getByText("Save Configuration"));

    const saveCall = intents.find(([, intent]) => intent === "saveApiConfiguration");
    expect(saveCall).toBeDefined();
    const [, , args] = saveCall as [string, string, unknown[]];
    expect(args[0]).toBe("OpenAI-Compatible");
    expect(args[2]).toBe("sk-super-secret");
    expect(screen.getByPlaceholderText("Enter your API key...")).toHaveValue("");
  });

  it("Reset API Settings does nothing on the first click - it only arms the confirmation", async () => {
    // Reset irreversibly wipes every provider's saved key, so it must not
    // be a single-click action. Legacy gated it behind a "This cannot be
    // undone" Yes/No prompt.
    const { user, push, intents } = setup();
    await goToApiEndpoint(user, push);

    await user.click(screen.getByText("Reset API Settings"));

    expect(intents.some(([, intent]) => intent === "resetApiSettings")).toBe(false);
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();
    expect(screen.getByText("Confirm Reset")).toBeInTheDocument();
  });

  it("Cancel backs out of an armed Reset without firing anything", async () => {
    const { user, push, intents } = setup();
    await goToApiEndpoint(user, push);

    await user.click(screen.getByText("Reset API Settings"));
    await user.click(screen.getByText("Cancel"));

    expect(intents.some(([, intent]) => intent === "resetApiSettings")).toBe(false);
    expect(screen.queryByText("Confirm Reset")).toBeNull();
    expect(screen.getByText("Reset API Settings")).toBeInTheDocument();
  });

  it("Confirm Reset fires resetApiSettings", async () => {
    const { user, push, intents } = setup();
    await goToApiEndpoint(user, push);

    await user.click(screen.getByText("Reset API Settings"));
    await user.click(screen.getByText("Confirm Reset"));

    expect(intents).toContainEqual(["app-settings", "resetApiSettings", []]);
  });

  it("Confirm Reset clears the local Base URL and model drafts too, not just the key", async () => {
    // Regression test: Reset originally only cleared draftApiKey, leaving
    // draftBaseUrl/draftModels showing their pre-reset values (the resync
    // effect only fires on a genuine provider switch, which Reset doesn't
    // cause) - a subsequent Save could silently re-persist exactly what
    // Reset was meant to clear.
    const { user, push } = setup();
    await goToApiEndpoint(user, push);

    await user.clear(screen.getByPlaceholderText("https://api.openai.com/v1"));
    await user.type(screen.getByPlaceholderText("https://api.openai.com/v1"), "https://custom-gateway.example/v1");
    await user.type(screen.getByLabelText("Chat, Explain, Takeaways (main model)"), "gpt-4o");

    await user.click(screen.getByText("Reset API Settings"));
    await user.click(screen.getByText("Confirm Reset"));

    expect(screen.getByPlaceholderText("https://api.openai.com/v1")).toHaveValue("https://api.openai.com/v1");
    expect(screen.getByLabelText("Chat, Explain, Takeaways (main model)")).toHaveValue("");
  });

  it("clicking a reasoning level radio fires setLlamaCppReasoningLevel", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    await user.click(screen.getByLabelText("Low"));

    expect(intents).toContainEqual(["app-settings", "setLlamaCppReasoningLevel", ["low"]]);
  });

  it("shows 'No model selected' when no chat model path is set", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push, { llamaCppChatModelPath: "" });

    expect(screen.getByText("No model selected")).toBeInTheDocument();
  });

  it("shows the current active GGUF's basename, not its full path", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push, { llamaCppChatModelPath: "C:\\models\\chat.gguf" });

    expect(screen.getByText("chat.gguf")).toBeInTheDocument();
  });

  it("System Scan fires scanLlamaCppSystem and disables while running", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push, { llamaCppScanStatus: "running" });

    expect(screen.getByText("Scanning...")).toBeDisabled();

    act(() => push({ ...snapshot, activeSection: "llama.cpp (local)", llamaCppScanStatus: "idle" }));
    await user.click(screen.getByText("System Scan"));

    expect(intents).toContainEqual(["app-settings", "scanLlamaCppSystem", []]);
  });

  it("Scan Folder... fires pickLlamaCppScanFolder (no longer a deferred placeholder)", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    await user.click(screen.getByText("Scan Folder..."));

    expect(intents).toContainEqual(["app-settings", "pickLlamaCppScanFolder", []]);
  });

  it("Scan Folder... is disabled while a scan is running", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push, { llamaCppScanStatus: "running" });

    expect(screen.getByText("Scan Folder...")).toBeDisabled();
  });

  it("the shared scanned-models datalist populates from llamaCppScannedModels", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push, { llamaCppScannedModels: ["C:/models/a.gguf", "C:/models/b.gguf"] });

    const datalist = document.getElementById("settings-llama-cpp-scanned-models");
    expect(datalist?.querySelectorAll("option")).toHaveLength(2);
  });

  it("the Scanned Chat Model select only appears once models are scanned, and selecting one fires setLlamaCppChatModelPath", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);
    expect(screen.queryByLabelText("Scanned Chat Model")).toBeNull();

    await goToLlamaCpp(user, push, { llamaCppScannedModels: ["C:/models/a.gguf"] });
    // basename(path) is what the option is actually labeled - see
    // CustomSelect's options={state.llamaCppScannedModels.map(...)} call in
    // SettingsDialog.tsx, not the raw path.
    await chooseCustomOption(user, "Scanned Chat Model", "a.gguf");

    expect(intents).toContainEqual(["app-settings", "setLlamaCppChatModelPath", ["C:/models/a.gguf"]]);
  });

  it("the Scanned Chat Model select shows the blank placeholder when the configured path isn't in the scanned list", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push, {
      llamaCppScannedModels: ["C:/models/a.gguf", "C:/models/b.gguf"],
      llamaCppChatModelPath: "C:/models/not-in-the-scanned-list.gguf",
    });

    // CustomSelect's trigger is a <button>, not a native <select> - its
    // "current value" is its own displayed text, not a .value DOM property.
    expect(screen.getByRole("button", { name: "Scanned Chat Model" })).toHaveTextContent("Select a scanned model...");
  });

  it("Chat Model File shows 'No file selected' by default and Browse fires pickLlamaCppChatModelFile", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    expect(screen.getByText("No file selected")).toBeInTheDocument();
    const browseButtons = screen.getAllByRole("button", { name: "Browse..." });
    await user.click(browseButtons[0]);

    expect(intents).toContainEqual(["app-settings", "pickLlamaCppChatModelFile", []]);
  });

  it("Chat Model File shows the staged path once one is set", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push, { llamaCppChatModelPath: "C:/models/chat.gguf" });

    expect(screen.getByText("C:/models/chat.gguf")).toBeInTheDocument();
  });

  it("Chat Naming File shows a reuse fallback by default and its own Browse fires pickLlamaCppTitleModelFile", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    expect(screen.getByText("Reusing the main chat model")).toBeInTheDocument();
    const browseButtons = screen.getAllByRole("button", { name: "Browse..." });
    await user.click(browseButtons[1]);

    expect(intents).toContainEqual(["app-settings", "pickLlamaCppTitleModelFile", []]);
  });

  it("Chat Format Override fires setLlamaCppChatFormat", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    await user.type(screen.getByLabelText("Chat Format Override"), "x");

    expect(intents).toContainEqual(["app-settings", "setLlamaCppChatFormat", ["x"]]);
  });

  it("Context Window fires setLlamaCppNCtx with the parsed number", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    const field = screen.getByLabelText("Context Window");
    await user.clear(field);
    await user.type(field, "8192");

    expect(intents).toContainEqual(["app-settings", "setLlamaCppNCtx", [8192]]);
  });

  it("GPU Layers fires setLlamaCppNGpuLayers with the parsed number", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    const field = screen.getByLabelText("GPU Layers");
    await user.clear(field);
    await user.type(field, "20");

    expect(intents).toContainEqual(["app-settings", "setLlamaCppNGpuLayers", [20]]);
  });

  it("CPU Threads fires setLlamaCppNThreads with the parsed number", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    const field = screen.getByLabelText("CPU Threads");
    await user.clear(field);
    await user.type(field, "4");

    expect(intents).toContainEqual(["app-settings", "setLlamaCppNThreads", [4]]);
  });

  it("an llamaCppNotice renders as an inline error", async () => {
    const { user, push } = setup();
    await goToLlamaCpp(user, push, { llamaCppNotice: "Chat Model File cannot be empty." });

    expect(screen.getByText("Chat Model File cannot be empty.")).toBeInTheDocument();
  });

  it("Save Settings fires saveLlamaCppSettings", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push);

    await user.click(screen.getByText("Save Settings"));

    expect(intents).toContainEqual(["app-settings", "saveLlamaCppSettings", []]);
  });

  it("Ollama's own Scan Folder... fires pickOllamaScanFolder (retroactively un-deferred by R7.4c)", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await user.click(screen.getByText("Scan Folder..."));

    expect(intents).toContainEqual(["app-settings", "pickOllamaScanFolder", []]);
  });

  it("Ollama's Scan Folder... disables while a scan is running", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { ollamaScanStatus: "running" });

    expect(screen.getByText("Scan Folder...")).toBeDisabled();
  });
});
