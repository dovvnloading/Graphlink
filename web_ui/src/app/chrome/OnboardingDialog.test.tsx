import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OnboardingDialog } from "./OnboardingDialog";
import { initialComposerState } from "./composerStore";
import { OverlayProvider } from "../overlays/overlays";
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
  apiModels: {},
  apiModelCatalog: [],
  apiCatalogStatus: "idle",
  apiCatalogMessage: "Model catalog has not been refreshed yet.",
  geminiStaticModels: [],
  geminiStaticImageModels: [],
  ollamaReasoningLevel: "high",
  ollamaCurrentModel: "",
  ollamaModelAssignments: {},
  ollamaScannedModels: [],
  ollamaScanSummary: "",
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
  llamaCppScanSummary: "",
  llamaCppScanStatus: "idle",
  llamaCppNotice: "",
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

function renderDialog(fixture: ReturnType<typeof makeFixture>) {
  return render(
    <OverlayProvider>
      <OnboardingDialog transport={fixture.transport} store={fixture.store} />
    </OverlayProvider>,
  );
}

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

    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Done" }));

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

    await user.click(screen.getByRole("button", { name: "Next" })); // welcome -> provider check

    expect(screen.getByText(/isn't ready yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open Settings" }));

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

  it("the provider-check step reports ready with no Open Settings button when route.available is true", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    act(() => fixture.pushComposer({ available: true, label: "Ollama (Local)" }));

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.getByText(/is ready/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Settings" })).toBeNull();
  });

  it("the sample-workspace step's button fires the scene-topic loadSampleWorkspace intent", async () => {
    const user = userEvent.setup();
    const fixture = makeFixture();
    renderDialog(fixture);
    act(() => fixture.pushSettings({ hasCompletedOnboarding: false }));
    act(() => fixture.pushComposer());

    await user.click(screen.getByRole("button", { name: "Next" })); // welcome -> provider check
    await user.click(screen.getByRole("button", { name: "Next" })); // provider check -> sample workspace
    await user.click(screen.getByRole("button", { name: "Load Sample Workspace" }));

    expect(fixture.intents).toContainEqual({ topic: "scene", intent: "loadSampleWorkspace", args: [] });
  });
});
