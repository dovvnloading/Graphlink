import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OnboardingDialog } from "./OnboardingDialog";
import { initialComposerState } from "./composerStore";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { SceneStore } from "../canvas/sceneStore";
import type { WsTransport } from "../../lib/ws/transport";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";

// ADR-012 stage 12.6: mirrors SettingsDialog.test.tsx's own "full valid
// snapshot object, spread with per-test overrides" fixture shape - every
// field is required by the app-settings-state contract's generated
// validator, so a real snapshot must be complete for TOPIC_VALIDATORS to
// accept it (an incomplete payload is silently rejected, not partially
// applied - see the component's own subscribe effect).
const settingsSnapshot: AppSettingsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  activeSection: "general",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: {},
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
  hasCompletedOnboarding: false,
  providerMode: "Ollama (Local)",
  activeApiProvider: "OpenAI-Compatible",
  viewingApiProvider: "OpenAI-Compatible",
  apiBaseUrl: "https://api.openai.com/v1",
  apiKeyConfigured: { openai: false, anthropic: false, gemini: false },
  apiKeySource: { openai: "none", anthropic: "none", gemini: "none" },
  apiProviders: [],
  apiModels: {},
  apiModelCatalog: [],
  apiCatalogBaseUrl: "",
  apiCatalogStatus: "idle",
  apiCatalogMessage: "Model catalog has not been refreshed yet.",
  geminiStaticModels: [],
  geminiStaticImageModels: [],
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
  ollamaScanSummary: "",
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
  llamaCppScanSummary: "",
  llamaCppScanStatus: "idle",
  llamaCppNotice: "",
  harnessTrustedDirs: [],
  mcpServers: [],
};

type Listener = (payload: Record<string, unknown>) => void;

function makeFixture() {
  const listeners = new Map<string, Listener>();
  const intents: Array<{ topic: string; intent: string; args: unknown[] }> = [];
  const transport = {
    subscribe: vi.fn((topic: string, listener: Listener) => {
      listeners.set(topic, listener);
      return () => listeners.delete(topic);
    }),
    intent: vi.fn(),
    fireIntent: vi.fn((topic: string, intent: string, args: unknown[] = []) => {
      intents.push({ topic, intent, args });
    }),
  } as unknown as WsTransport;
  const store = new SceneStore(transport);
  return {
    transport,
    intents,
    store,
    pushSettings: (overrides: Partial<AppSettingsState> = {}) =>
      listeners.get("app-settings")?.({ ...settingsSnapshot, ...overrides } as unknown as Record<string, unknown>),
    pushComposer: (routeOverrides: Partial<typeof initialComposerState.route> = {}) =>
      listeners
        .get("app-composer")
        ?.({ ...initialComposerState, route: { ...initialComposerState.route, ...routeOverrides } } as unknown as Record<
          string,
          unknown
        >),
  };
}

/** Stands in for the Settings dialog's own close button. */
function CloseSettingsProbe() {
  const overlays = useOverlays();
  return overlays.isOpen("settings")
    ? <button type="button" onClick={() => overlays.close()}>close settings</button> : null;
}

function renderDialog(fixture: ReturnType<typeof makeFixture>) {
  return render(
    <OverlayProvider>
      <OnboardingDialog transport={fixture.transport} store={fixture.store} />
      <CloseSettingsProbe />
    </OverlayProvider>,
  );
}

const completed = (fixture: ReturnType<typeof makeFixture>) =>
  fixture.intents.some((entry) => entry.intent === "setHasCompletedOnboarding");

describe("OnboardingDialog (ADR-012 stage 12.6)", () => {
  it("auto-opens on a fresh machine (hasCompletedOnboarding false)", () => {
    const fixture = makeFixture();
    renderDialog(fixture);

    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));

    expect(screen.getByRole("dialog", { name: "Welcome to Graphlink" })).toBeInTheDocument();
  });

  it("does not auto-open once onboarding has already been completed", () => {
    const fixture = makeFixture();
    renderDialog(fixture);

    act(() => fixture.pushSettings({ hasCompletedOnboarding: true }));

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closing the wizard persists hasCompletedOnboarding via setHasCompletedOnboarding", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fixture.intents).toContainEqual({
      topic: "app-settings",
      intent: "setHasCompletedOnboarding",
      args: [true],
    });
  });

  it("the provider-check step reports \"not ready\" and offers Open Settings when route.available is false", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false, providerMode: "Ollama (Local)" }));
    act(() => fixture.pushComposer({ available: false, label: "Ollama (Local)" }));

    await user.click(await screen.findByRole("button", { name: "Next" })); // welcome -> provider check

    expect(screen.getByText(/isn't ready yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open settings" }));

    expect(fixture.intents).toContainEqual({
      topic: "app-settings",
      intent: "setActiveSection",
      args: ["ollama (local)"],
    });
    // Single-open (overlays.tsx): opening Settings closes onboarding - no
    // real <SettingsDialog> is mounted in this test, so the observable proof
    // is that onboarding's OWN dialog is gone, not that Settings' is up.
    expect(screen.queryByRole("dialog", { name: "Welcome to Graphlink" })).toBeNull();
  });

  it("opens the cloud directory directly from first-run local setup", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false, providerMode: "Ollama (Local)" }));
    act(() => fixture.pushComposer({ available: false }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Connect a cloud provider" }));
    expect(fixture.intents).toContainEqual({ topic: "app-settings", intent: "setActiveSection", args: ["api endpoint"] });
  });

  it("the provider-check step reports ready with no Open Settings button when route.available is true", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    act(() => fixture.pushComposer({ available: true, label: "Ollama (Local)", modelId: "llama3" }));

    await user.click(await screen.findByRole("button", { name: "Next" }));

    expect(screen.getByText(/is ready/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open settings" })).toBeInTheDocument();
  });

  it("an available route with no chat model is not ready, on both steps (#1131)", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false, providerMode: "API Endpoint" }));
    act(() => fixture.pushComposer({ available: true, label: "OpenAI-Compatible", modelId: "" }));

    await user.click(await screen.findByRole("button", { name: "Next" }));
    expect(screen.getByText(/No chat model is selected for OpenAI-Compatible/)).toBeInTheDocument();
    expect(screen.queryByText(/is ready/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText(/Choose a chat model before you send/)).toBeInTheDocument();
    expect(screen.queryByText("You're set.")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Set up a model" }));
    expect(fixture.intents).toContainEqual({ topic: "app-settings", intent: "setActiveSection", args: ["api endpoint"] });
  });

  it("an unavailable local route is still reported as not ready (#1131)", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    act(() => fixture.pushComposer({ available: false, label: "Ollama (Local)", modelId: "llama3" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    expect(screen.getByText(/isn't ready yet/)).toBeInTheDocument();
  });

  it("provider setup pauses the wizard and resumes it at the Model step (#1129)", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    act(() => fixture.pushComposer({ available: false, label: "Ollama (Local)" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    expect(screen.queryByRole("dialog", { name: "Welcome to Graphlink" })).toBeNull();
    expect(completed(fixture)).toBe(false);

    // The model gets configured while Settings is open, then Settings closes.
    act(() => fixture.pushComposer({ available: true, label: "Ollama (Local)", modelId: "llama3" }));
    await user.click(screen.getByRole("button", { name: "close settings" }));
    expect(await screen.findByRole("dialog", { name: "Welcome to Graphlink" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Model" })).toBeInTheDocument();
    expect(screen.getByText(/is ready/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("You're set.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Finish" }));
    expect(completed(fixture)).toBe(true);
  });

  it("cancelling provider setup returns to the wizard once, and Skip still completes it (#1129)", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    act(() => fixture.pushComposer({ available: false }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Connect a cloud provider" }));
    await user.click(screen.getByRole("button", { name: "close settings" }));

    expect(await screen.findByRole("dialog", { name: "Welcome to Graphlink" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Model" })).toBeInTheDocument();
    expect(completed(fixture)).toBe(false);
    await user.click(screen.getByRole("button", { name: "Skip setup" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(completed(fixture)).toBe(true);
  });

  it("Escape is a dismissal that completes onboarding, not a pause (#1129)", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    await screen.findByRole("button", { name: "Next" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(completed(fixture)).toBe(true);
  });

  it("the sample-workspace step's button fires the scene-topic loadSampleWorkspace intent", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    act(() => fixture.pushComposer());

    await user.click(await screen.findByRole("button", { name: "Next" })); // welcome -> provider check
    await user.click(await screen.findByRole("button", { name: "Next" })); // provider check -> sample workspace
    await user.click(screen.getByRole("button", { name: "Load sample workspace" }));

    expect(fixture.intents).toContainEqual({ topic: "scene", intent: "loadSampleWorkspace", args: [] });
  });
});
