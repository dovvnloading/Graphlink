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
  theme: "dark",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: { info: true, success: true, warning: true, error: true },
  githubTokenConfigured: false,
  activeApiProvider: "OpenAI-Compatible",
  viewingApiProvider: "OpenAI-Compatible",
  apiBaseUrl: "https://api.openai.com/v1",
  apiKeyConfigured: { openai: false, anthropic: false, gemini: false },
  apiModels: {},
  apiModelCatalog: [],
  apiCatalogStatus: "idle",
  apiCatalogMessage: "Model catalog has not been refreshed yet.",
  geminiStaticModels: ["gemini-2.5-flash", "gemini-2.5-pro"],
  geminiStaticImageModels: ["gemini-2.5-flash-image"],
  ollamaReasoningMode: "Thinking",
  ollamaCurrentModel: "",
  ollamaModelAssignments: {},
  ollamaScannedModels: [],
  ollamaScanSummary: "No saved scan yet. Run a system scan to build the local model list.",
  ollamaScanStatus: "idle",
  ollamaPullStatus: "idle",
  ollamaNotice: "",
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

  it("Llama.cpp still renders the deferred placeholder", async () => {
    const { user, push } = setup();
    await user.click(screen.getByText("open settings"));
    await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));
    act(() => push({ ...snapshot, activeSection: "llama.cpp (local)" }));

    expect(screen.getByText("Llama.cpp (Local) configuration lands in R7.4c.")).toBeInTheDocument();
  });

  it("Ollama page renders for the real (not deferred-placeholder) section", async () => {
    const { user, push } = setup();
    await goToOllama(user, push);

    expect(screen.queryByText(/lands in R7\.4c/)).toBeNull();
    expect(screen.getByText("Reasoning Mode")).toBeInTheDocument();
  });

  it("clicking a reasoning mode radio fires setOllamaReasoningMode", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await user.click(screen.getByLabelText("Quick Mode (No CoT)"));

    expect(intents).toContainEqual(["app-settings", "setOllamaReasoningMode", ["Quick"]]);
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

  it("Scan Folder... is disabled - deferred to R7.4c's native picker", async () => {
    const { user, push } = setup();
    await goToOllama(user, push);

    expect(screen.getByText("Scan Folder...")).toBeDisabled();
  });

  it("a per-task select defaults to auto and switching to inherit fires setOllamaModelAssignment immediately", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await user.selectOptions(screen.getByLabelText("Chat Naming Model"), "Use chat model");

    expect(intents).toContainEqual(["app-settings", "setOllamaModelAssignment", ["task_title", "inherit"]]);
  });

  it("task_chat has no 'Use chat model' inherit option (it IS the chat model)", async () => {
    const { user, push } = setup();
    await goToOllama(user, push);

    const chatSelect = screen.getByLabelText("Chat Model") as HTMLSelectElement;
    const optionLabels = Array.from(chatSelect.options).map((o) => o.textContent);
    expect(optionLabels).not.toContain("Use chat model");
  });

  it("switching a task to explicit reveals a text field; typing fires setOllamaModelAssignment", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push);

    await user.selectOptions(screen.getByLabelText("Chart Generation Model"), "Custom model ID...");
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

  it("switching the provider select fires setViewingApiProvider", async () => {
    const { user, push, intents } = setup();
    await goToApiEndpoint(user, push);

    await user.selectOptions(screen.getByLabelText("API Provider"), "Anthropic Claude");

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
});
