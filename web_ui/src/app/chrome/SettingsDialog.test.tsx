import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SettingsDialog, parseEnvLines } from "./SettingsDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { ExecutionLimitsProvider } from "../canvas/ExecutionLimitsContext";
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
  // ADR-012 stage 12.6: required by the app-settings-state contract.
  mcpServers: [],
};

function makeTransport() {
  const intents: unknown[][] = [];
  // Topic-keyed, not a single shared listener slot - ADR-012 stage 12.6's
  // new Resource Limits section pulls in ExecutionLimitsProvider (below),
  // which subscribes to its own "execution-limits" topic on mount alongside
  // this dialog's own "app-settings" subscribe. Mirrors
  // ExecutionLimitsContext.test.tsx's own makeFakeTransport shape.
  const listeners = new Map<string, (payload: Record<string, unknown>) => void>();
  const transport = {
    subscribe: (topic: string, l: (payload: Record<string, unknown>) => void) => {
      listeners.set(topic, l);
      return () => {
        listeners.delete(topic);
      };
    },
    intent: (topic: string, intent: string, args: unknown[]) => {
      intents.push([topic, intent, args]);
    },
    // ADR-003 stage 3.1: SettingsDialog's own mutating call sites now go
    // through fireIntent, not the bare intent() above.
    fireIntent: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
    },
  } as unknown as WsTransport;
  return {
    transport,
    intents,
    push: (payload: Record<string, unknown>) => listeners.get("app-settings")?.(payload),
    pushExecutionLimits: (payload: Record<string, unknown>) => listeners.get("execution-limits")?.(payload),
    // ADR-014 stage 14.4: the Plugins page reads a SECOND, independent
    // subscription ("app-plugins", the same topic PluginPicker.tsx already
    // reads) - separate from the dialog's own "app-settings" push above.
    pushPlugins: (payload: Record<string, unknown>) => listeners.get("app-plugins")?.(payload),
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
      {/* Mirrors App.tsx's own real wiring (ADR-012 stage 12.6 widened
          ExecutionLimitsProvider's scope to cover SettingsDialog, not just
          SceneCanvas) - without this ancestor, useExecutionLimits() inside
          the new Resource Limits page would silently fall back to blank
          text rather than exercising the real subscribe path. */}
      <ExecutionLimitsProvider transport={fake.transport}>
        <SettingsDialog transport={fake.transport} />
      </ExecutionLimitsProvider>
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

async function goToResourceLimits(
  user: ReturnType<typeof userEvent.setup>,
  push: (payload: Record<string, unknown>) => void,
  overrides: Record<string, unknown> = {},
) {
  await user.click(screen.getByText("open settings"));
  await user.click(screen.getByRole("button", { name: "Resource Limits" }));
  act(() => push({ ...snapshot, ...overrides, activeSection: "resource limits" }));
}

async function goToMcpServers(
  user: ReturnType<typeof userEvent.setup>,
  push: (payload: Record<string, unknown>) => void,
  overrides: Record<string, unknown> = {},
) {
  await user.click(screen.getByText("open settings"));
  await user.click(screen.getByRole("button", { name: "MCP Servers" }));
  act(() => push({ ...snapshot, ...overrides, activeSection: "mcp servers" }));
}

async function goToPlugins(
  user: ReturnType<typeof userEvent.setup>,
  push: (payload: Record<string, unknown>) => void,
  overrides: Record<string, unknown> = {},
) {
  await user.click(screen.getByText("open settings"));
  await user.click(screen.getByRole("button", { name: "Plugins" }));
  act(() => push({ ...snapshot, ...overrides, activeSection: "plugins" }));
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

  it("General page renders the auto model policy select at its current value", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open settings"));

    expect(screen.getByRole("button", { name: "Automatic Model Selection Policy" })).toHaveTextContent(
      "Cheapest Capable",
    );
  });

  it("choosing an auto model policy option fires setAutoModelPolicy with the option's id", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open settings"));

    await chooseCustomOption(user, "Automatic Model Selection Policy", "Best Quality");

    expect(intents).toContainEqual(["app-settings", "setAutoModelPolicy", ["best-quality"]]);
  });

  it("General page renders the theme select at its current value", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open settings"));

    expect(screen.getByRole("button", { name: "Theme" })).toHaveTextContent("Match System");
  });

  it("choosing a theme option fires setTheme with the option's id", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open settings"));

    await chooseCustomOption(user, "Theme", "Dark");

    expect(intents).toContainEqual(["app-settings", "setTheme", ["dark"]]);
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

    // ADR-006 stage 6.5: Image Generation is deliberately absent from this
    // list - it is optional for every provider now (see the test below).
    for (const label of [
      "Chat Naming / Session Title",
      "Chat, Explain, Takeaways (main model)",
      "Chart Generation (code-capable model)",
      "Web Content Validation",
      "Web Content Summarization",
    ]) {
      await user.type(screen.getByLabelText(label), "gpt-4o-mini");
    }

    expect(saveButton).toBeEnabled();
  });

  it("Image Generation is visible but optional for OpenAI-Compatible (capability-gated at call time)", async () => {
    // ADR-006 stage 6.5: a text-only OpenAI-compatible endpoint (vLLM,
    // LM Studio, llama-server) must save cleanly without an image model -
    // the backend raises an actionable error at image-generation time
    // instead of blocking Save up front.
    const { user, push } = setup();
    await goToApiEndpoint(user, push);

    // Still rendered (unlike Anthropic, which hides it entirely), labeled
    // optional the same way the Llama.cpp page labels its optional field.
    expect(screen.getByLabelText("Image Generation (optional)")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Enter your API key..."), "sk-test");
    for (const label of [
      "Chat Naming / Session Title",
      "Chat, Explain, Takeaways (main model)",
      "Chart Generation (code-capable model)",
      "Web Content Validation",
      "Web Content Summarization",
    ]) {
      await user.type(screen.getByLabelText(label), "gpt-4o-mini");
    }

    expect(screen.getByText("Save Configuration")).toBeEnabled();
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
      "Image Generation (optional)",
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

  // ADR-012 stage 12.6: the provider-mode switcher (ProviderModeSwitch in
  // SettingsDialog.tsx) - previously nothing in the frontend ever called
  // ADR-006 stage 6.5's setProviderMode intent at all. One component shared
  // by all 3 mode pages, so one representative "reflects the active mode"
  // case plus one "switch fires the right intent" case per page is enough
  // to pin both the read side (state.providerMode) and the write side
  // (which mode string each page's own button sends) without re-testing
  // ProviderModeSwitch's internals 3 times over.
  it("Ollama page shows itself as the active provider mode and renders no switch button when it already is", async () => {
    const { user, push } = setup();
    await goToOllama(user, push, { providerMode: "Ollama (Local)" });

    expect(screen.getByText("Ollama (Local) is the active provider mode.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use This Provider" })).toBeNull();
  });

  it("Ollama page offers a switch action when a different mode is active, and it fires setProviderMode", async () => {
    const { user, push, intents } = setup();
    await goToOllama(user, push, { providerMode: "API Endpoint" });

    expect(
      screen.getByText("Ollama (Local) is configured here, but API Endpoint is the active provider mode."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use This Provider" }));

    expect(intents).toContainEqual(["app-settings", "setProviderMode", ["Ollama (Local)"]]);
  });

  it("Llama.cpp page offers a switch action when a different mode is active, and it fires setProviderMode", async () => {
    const { user, push, intents } = setup();
    await goToLlamaCpp(user, push, { providerMode: "Ollama (Local)" });

    expect(
      screen.getByText("Llama.cpp (Local) is configured here, but Ollama (Local) is the active provider mode."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use This Provider" }));

    expect(intents).toContainEqual(["app-settings", "setProviderMode", ["Llama.cpp (Local)"]]);
  });

  it("API Endpoint page shows itself as the active provider mode and renders no switch button when it already is", async () => {
    const { user, push } = setup();
    await goToApiEndpoint(user, push, { providerMode: "API Endpoint" });

    expect(screen.getByText("API Endpoint is the active provider mode.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use This Provider" })).toBeNull();
  });

  it("API Endpoint page offers a switch action when a different mode is active, and it fires setProviderMode", async () => {
    const { user, push, intents } = setup();
    await goToApiEndpoint(user, push, { providerMode: "Ollama (Local)" });

    await user.click(screen.getByRole("button", { name: "Use This Provider" }));

    expect(intents).toContainEqual(["app-settings", "setProviderMode", ["API Endpoint"]]);
  });

  // ADR-012 stage 12.6: the new read-only Resource Limits section - the SAME
  // useExecutionLimits() disclosure CodeExecutionApprovalPanel.tsx renders
  // before a human approves code execution, made reachable ahead of time
  // instead of only mid-approval. No fireIntent call exists on this page at
  // all (see ResourceLimitsPage's own doc comment), so unlike every other
  // section above there is nothing here to pin an intents assertion to -
  // these tests only cover rendering.
  it("navigating to Resource Limits fires setActiveSection with its own key", async () => {
    const { user, push, intents } = setup();
    await goToResourceLimits(user, push);

    expect(intents).toContainEqual(["app-settings", "setActiveSection", ["resource limits"]]);
  });

  it("Resource Limits page renders both the Py-Coder and Virtual Environment Runner disclosure text", async () => {
    const { user, push, pushExecutionLimits } = setup();
    await goToResourceLimits(user, push);
    act(() =>
      pushExecutionLimits({
        schemaVersion: 1,
        minCompatibleSchemaVersion: 1,
        revision: 1,
        pycoderResourceLimitsText: "Execution is capped at approximately 2 GB of memory and 64 concurrent processes.",
        codeSandboxResourceLimitsText:
          "Execution is capped at approximately 2 GB of memory and 64 concurrent processes. Binary packages only.",
      }),
    );

    expect(
      screen.getByText("Execution is capped at approximately 2 GB of memory and 64 concurrent processes."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Execution is capped at approximately 2 GB of memory and 64 concurrent processes. Binary packages only.",
      ),
    ).toBeInTheDocument();
  });

  it("Resource Limits page falls back to a placeholder message before any snapshot arrives", async () => {
    const { user, push } = setup();
    await goToResourceLimits(user, push);

    expect(screen.getByText("No resource limits have been reported by the backend yet.")).toBeInTheDocument();
  });

  // ADR-012 stage 12.6: the MCP Servers page - the deferred UI half of
  // ADR-007 stage 7.5's MCP client. Every mutation sends the FULL updated
  // array via setMcpServers (bulk-replace, matching SettingsManager.
  // set_mcp_servers' own "replace the whole collection" posture), so each
  // mutating test below pins the exact resulting array, not just that SOME
  // intent fired.
  describe("MCP Servers page", () => {
    const fsServer = {
      id: "fs-id",
      name: "fs",
      command: "npx",
      args: ["-y", "server-filesystem"],
      scopes: [],
      approval: "always",
      enabledTools: [],
      enabled: true,
      timeout: 30,
      envKeys: [],
    };

    it("navigating to MCP Servers fires setActiveSection with its own key", async () => {
      const { user, push, intents } = setup();
      await goToMcpServers(user, push);

      expect(intents).toContainEqual(["app-settings", "setActiveSection", ["mcp servers"]]);
    });

    it("shows a placeholder when no servers are configured", async () => {
      const { user, push } = setup();
      await goToMcpServers(user, push);

      expect(screen.getByText("No MCP servers configured yet.")).toBeInTheDocument();
    });

    it("lists a configured server's name, command+args, and enabled state", async () => {
      const { user, push } = setup();
      await goToMcpServers(user, push, { mcpServers: [fsServer] });

      expect(screen.getByText("fs")).toBeInTheDocument();
      expect(screen.getByText("npx -y server-filesystem")).toBeInTheDocument();
      expect(screen.getByRole("checkbox")).toBeChecked();
    });

    it("unchecking a server's enabled toggle fires setMcpServers with the full array, that entry flipped", async () => {
      const { user, push, intents } = setup();
      await goToMcpServers(user, push, { mcpServers: [fsServer] });

      await user.click(screen.getByRole("checkbox"));

      expect(intents).toContainEqual([
        "app-settings",
        "setMcpServers",
        [[{ ...fsServer, enabled: false }]],
      ]);
    });

    it("Add Server stays disabled until both a name and a command are filled in", async () => {
      const { user, push } = setup();
      await goToMcpServers(user, push);

      const addButton = screen.getByRole("button", { name: "Add Server" });
      expect(addButton).toBeDisabled();

      await user.type(screen.getByLabelText("Server Name"), "git");
      expect(addButton).toBeDisabled();

      await user.type(screen.getByLabelText("Command"), "uvx");
      expect(addButton).toBeEnabled();
    });

    it("Add Server fires setMcpServers with the full array plus the new entry (args split on whitespace), and clears the draft", async () => {
      const { user, push, intents } = setup();
      await goToMcpServers(user, push, { mcpServers: [fsServer] });

      await user.type(screen.getByLabelText("Server Name"), "git");
      await user.type(screen.getByLabelText("Command"), "uvx");
      await user.type(screen.getByLabelText("Arguments (optional, space-separated)"), "mcp-server-git --repo /tmp");
      await user.click(screen.getByRole("button", { name: "Add Server" }));

      expect(intents).toContainEqual([
        "app-settings",
        "setMcpServers",
        [[
          fsServer,
          {
            name: "git",
            command: "uvx",
            args: ["mcp-server-git", "--repo", "/tmp"],
            scopes: [],
            approval: "always",
            enabledTools: [],
            enabled: true,
            timeout: 30,
            // The add path is the one place env VALUES go out (the user just
            // typed them); every other edit omits `env` entirely so the
            // backend keeps what is stored. Empty here because this test
            // typed no variables.
            envKeys: [],
            env: {},
          },
        ]],
      ]);
      expect(screen.getByLabelText("Server Name")).toHaveValue("");
      expect(screen.getByLabelText("Command")).toHaveValue("");
      expect(screen.getByLabelText("Arguments (optional, space-separated)")).toHaveValue("");
    });

    it('Add Server with no arguments typed sends an empty args array, not [""]', async () => {
      const { user, push, intents } = setup();
      await goToMcpServers(user, push);

      await user.type(screen.getByLabelText("Server Name"), "git");
      await user.type(screen.getByLabelText("Command"), "uvx");
      await user.click(screen.getByRole("button", { name: "Add Server" }));

      const addCall = intents.find(([, intent]) => intent === "setMcpServers");
      expect(addCall).toBeDefined();
      const [, , args] = addCall as [string, string, unknown[]];
      const [addedServers] = args as [{ name: string; args: string[] }[]];
      expect(addedServers[0].args).toEqual([]);
    });

    it("Remove fires setMcpServers with the full array minus that entry", async () => {
      const gitServer = {
        id: "git-id",
        name: "git",
        command: "uvx",
        args: [],
        scopes: [],
        approval: "always",
        enabledTools: [],
        enabled: true,
        timeout: 30,
        envKeys: [],
      };
      const { user, push, intents } = setup();
      await goToMcpServers(user, push, { mcpServers: [fsServer, gitServer] });

      await user.click(screen.getByRole("button", { name: "Remove fs" }));

      expect(intents).toContainEqual(["app-settings", "setMcpServers", [[gitServer]]]);
    });
  });

  // ADR-014 stage 14.4: install-time consent for discovered third-party
  // plugins. Reads a SEPARATE topic ("app-plugins", via pushPlugins) than
  // every other section in this file - each test below pushes both the
  // "app-settings" snapshot (to select the Plugins section) and, where
  // needed, a distinct "app-plugins" snapshot carrying the new `grants`
  // field, mirroring how the Resource Limits tests above push
  // "execution-limits" independently of "app-settings".
  describe("Plugins page", () => {
    const helloGrant = { pluginId: "hello_node", name: "Hello Node", scopes: ["graph.mutate"], granted: false };
    const counterGrant = {
      pluginId: "counter_node",
      name: "Counter Node",
      scopes: ["graph.mutate", "graph.read"],
      granted: true,
    };

    function pushGrants(pushPlugins: (payload: Record<string, unknown>) => void, grants: unknown[]) {
      act(() =>
        pushPlugins({ schemaVersion: 1, minCompatibleSchemaVersion: 1, revision: 1, categories: [], grants }),
      );
    }

    it("navigating to Plugins fires setActiveSection with its own key", async () => {
      const { user, push, intents } = setup();
      await goToPlugins(user, push);

      expect(intents).toContainEqual(["app-settings", "setActiveSection", ["plugins"]]);
    });

    it("renders sensibly before any app-plugins snapshot has arrived", async () => {
      const { user, push } = setup();
      await goToPlugins(user, push);

      expect(screen.getByText("No third-party plugins need a grant right now.")).toBeInTheDocument();
      expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    });

    it("an empty grants array renders sensibly - no crash, no phantom rows", async () => {
      const { user, push, pushPlugins } = setup();
      await goToPlugins(user, push);
      pushGrants(pushPlugins, []);

      expect(screen.getByText("No third-party plugins need a grant right now.")).toBeInTheDocument();
      expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    });

    it("renders one row per grants entry with its name, declared scopes, and current granted state", async () => {
      const { user, push, pushPlugins } = setup();
      await goToPlugins(user, push);
      pushGrants(pushPlugins, [helloGrant, counterGrant]);

      expect(screen.getByText("Hello Node")).toBeInTheDocument();
      expect(screen.getByText("graph.mutate")).toBeInTheDocument();
      expect(screen.getByText("Counter Node")).toBeInTheDocument();
      expect(screen.getByText("graph.mutate, graph.read")).toBeInTheDocument();

      expect(screen.getByRole("checkbox", { name: "Granted: Hello Node" })).not.toBeChecked();
      expect(screen.getByRole("checkbox", { name: "Granted: Counter Node" })).toBeChecked();
    });

    it("a plugin with no declared scopes shows a 'No declared scopes' placeholder, not an empty string", async () => {
      const { user, push, pushPlugins } = setup();
      await goToPlugins(user, push);
      pushGrants(pushPlugins, [{ pluginId: "bare_plugin", name: "Bare Plugin", scopes: [], granted: false }]);

      expect(screen.getByText("No declared scopes")).toBeInTheDocument();
    });

    it("checking an ungranted plugin's checkbox fires setPluginGrant with its pluginId and true", async () => {
      const { user, push, pushPlugins, intents } = setup();
      await goToPlugins(user, push);
      pushGrants(pushPlugins, [helloGrant]);

      await user.click(screen.getByRole("checkbox", { name: "Granted: Hello Node" }));

      expect(intents).toContainEqual(["app-plugins", "setPluginGrant", ["hello_node", true]]);
    });

    it("unchecking an already-granted plugin's checkbox fires setPluginGrant with its pluginId and false", async () => {
      const { user, push, pushPlugins, intents } = setup();
      await goToPlugins(user, push);
      pushGrants(pushPlugins, [counterGrant]);

      await user.click(screen.getByRole("checkbox", { name: "Granted: Counter Node" }));

      expect(intents).toContainEqual(["app-plugins", "setPluginGrant", ["counter_node", false]]);
    });
  });
});

describe("parseEnvLines (MCP server env field)", () => {
  it("parses KEY=value lines into a record", () => {
    expect(parseEnvLines("GITHUB_TOKEN=ghp_abc\nBRAVE_API_KEY=brv")).toEqual({
      GITHUB_TOKEN: "ghp_abc",
      BRAVE_API_KEY: "brv",
    });
  });

  it("splits on the FIRST '=' only, so a value can itself contain '=' (base64 tokens do)", () => {
    expect(parseEnvLines("TOKEN=abc==")).toEqual({ TOKEN: "abc==" });
  });

  it("skips blank lines, lines without '=', and lines with an empty key", () => {
    expect(parseEnvLines("\n\nnot-a-pair\n=orphan-value\n  \nOK=1\r\n")).toEqual({ OK: "1" });
  });

  it("trims whitespace around keys and values and tolerates CRLF", () => {
    expect(parseEnvLines("  A = 1 \r\n B=2\r\n")).toEqual({ A: "1", B: "2" });
  });
});
