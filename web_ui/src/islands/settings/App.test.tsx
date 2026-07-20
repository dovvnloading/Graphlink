import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialSettingsState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createSettingsBridge() falls through
// to the mock bridge automatically for the smoke test below - same pattern
// as every other island's App.test.tsx.

describe("App against the mock bridge", () => {
  it("renders all 5 rail sections with General active", () => {
    render(<App />);

    expect(screen.getByRole("button", { name: "General" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Ollama (Local)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Llama.cpp (Local)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "API Endpoint" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Integrations" })).toBeInTheDocument();
  });

  it("clicking a rail button navigates to that section", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Integrations" }));

    expect(screen.getByRole("button", { name: "Integrations" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "General" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("region", { name: "Integrations" })).toBeInTheDocument();
  });

  it("General renders the theme select and all 4 notification checkboxes, all reflecting initial state", () => {
    render(<App />);

    expect(screen.getByLabelText("Theme")).toHaveValue("dark");
    expect(screen.getByRole("checkbox", { name: "Show Token Counter Overlay" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Enable Assistant System Prompt" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Info" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Success" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Warning" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Error" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Enable Update Notifications on Startup" })).not.toBeChecked();
    expect(screen.getByText("Automatic update checks are off.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check for Updates" })).toBeEnabled();
  });

  it("Check for Updates reports a finished check via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Check for Updates" }));

    await waitFor(() => expect(screen.getByText(/You're up to date\./)).toBeInTheDocument());
  });

  it("Open Repository does not throw via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Open Repository" }));
  });

  it("changing the theme select updates state via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("Theme"), "muted");

    expect(screen.getByLabelText("Theme")).toHaveValue("muted");
  });

  it("unchecking a notification type updates only that type", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("checkbox", { name: "Warning" }));

    expect(screen.getByRole("checkbox", { name: "Warning" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Info" })).toBeChecked();
  });

  it("Integrations renders an empty, write-only token field and the not-configured status", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Integrations" }));

    expect(screen.getByLabelText("GitHub Personal Access Token")).toHaveValue("");
    expect(screen.getByLabelText("GitHub Personal Access Token")).toHaveAttribute("type", "password");
    expect(screen.getByText("No GitHub token configured.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Integrations" })).toBeDisabled();
  });

  it("typing a token enables Save, and saving clears the draft field via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Integrations" }));

    await user.type(screen.getByLabelText("GitHub Personal Access Token"), "ghp_typed");
    expect(screen.getByRole("button", { name: "Save Integrations" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Save Integrations" }));

    await waitFor(() => expect(screen.getByText("A GitHub token is currently configured.")).toBeInTheDocument());
    expect(screen.getByLabelText("GitHub Personal Access Token")).toHaveValue("");
  });

  it("Clear Token resets the status to not-configured via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Integrations" }));
    await user.type(screen.getByLabelText("GitHub Personal Access Token"), "ghp_typed");
    await user.click(screen.getByRole("button", { name: "Save Integrations" }));
    await waitFor(() => expect(screen.getByText("A GitHub token is currently configured.")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Clear Token" }));

    await waitFor(() => expect(screen.getByText("No GitHub token configured.")).toBeInTheDocument());
  });

  it("API Endpoint defaults to OpenAI-Compatible with the Base URL field visible", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));

    expect(screen.getByLabelText("API Provider")).toHaveValue("OpenAI-Compatible");
    expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
    expect(screen.getByText("No key configured for this provider.")).toBeInTheDocument();
    expect(screen.getByLabelText("Image Generation")).toBeInTheDocument();
  });

  it("switching provider to Anthropic hides Base URL, keeps the Load button (live catalog fetch), and excludes image gen", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));

    await user.selectOptions(screen.getByLabelText("API Provider"), "Anthropic Claude");

    expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument();
    // Anthropic performs a real live catalog fetch, so the Load button must
    // be present (matches legacy load_btn visible for OpenAI or Anthropic).
    expect(screen.getByRole("button", { name: "Load Available Models" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Image Generation")).not.toBeInTheDocument();
    expect(screen.getByText("Anthropic Claude does not support image generation in Graphlink yet.")).toBeInTheDocument();
  });

  it("switching provider to Gemini hides both Base URL and the Load button (Gemini uses static lists)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));

    await user.selectOptions(screen.getByLabelText("API Provider"), "Google Gemini");

    expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument();
    // Gemini has no live catalog to fetch, so no Load button (matches legacy).
    expect(screen.queryByRole("button", { name: "Load Available Models" })).not.toBeInTheDocument();
  });

  it("Load Available Models is disabled until a key is typed", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));

    expect(screen.getByRole("button", { name: "Load Available Models" })).toBeDisabled();

    await user.type(screen.getByLabelText("API Key"), "sk-typed");

    expect(screen.getByRole("button", { name: "Load Available Models" })).toBeEnabled();
  });

  it("Save Configuration reports configured and clears the key field via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));

    await user.type(screen.getByLabelText("API Key"), "sk-typed");
    await user.click(screen.getByRole("button", { name: "Save Configuration" }));

    await waitFor(() =>
      expect(screen.getByText("A key is currently configured for this provider.")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("API Key")).toHaveValue("");
  });

  it("Reset API Settings requires a confirmation step before clearing", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));
    await user.type(screen.getByLabelText("API Key"), "sk-typed");
    await user.click(screen.getByRole("button", { name: "Save Configuration" }));
    await waitFor(() =>
      expect(screen.getByText("A key is currently configured for this provider.")).toBeInTheDocument(),
    );

    // First click only arms the confirmation - nothing is cleared yet.
    await user.click(screen.getByRole("button", { name: "Reset API Settings" }));
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();
    expect(screen.getByText("A key is currently configured for this provider.")).toBeInTheDocument();

    // Confirm actually clears.
    await user.click(screen.getByRole("button", { name: "Confirm Reset" }));
    await waitFor(() =>
      expect(screen.getByText("No key configured for this provider.")).toBeInTheDocument(),
    );
  });

  it("Cancelling the reset confirmation leaves the configuration intact", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));
    await user.type(screen.getByLabelText("API Key"), "sk-typed");
    await user.click(screen.getByRole("button", { name: "Save Configuration" }));
    await waitFor(() =>
      expect(screen.getByText("A key is currently configured for this provider.")).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Reset API Settings" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByText(/cannot be undone/)).not.toBeInTheDocument();
    expect(screen.getByText("A key is currently configured for this provider.")).toBeInTheDocument();
  });

  it("Gemini's Image Generation field points at the curated image-model datalist", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "API Endpoint" }));

    // OpenAI (default): image field uses the shared datalist.
    expect(screen.getByLabelText("Image Generation")).toHaveAttribute("list", "settings-api-available-models");

    await user.selectOptions(screen.getByLabelText("API Provider"), "Google Gemini");

    // Gemini: image field switches to the separate curated image datalist,
    // and that datalist carries the Gemini image models (not chat models).
    expect(screen.getByLabelText("Image Generation")).toHaveAttribute("list", "settings-api-image-models");
    const imageDatalist = document.getElementById("settings-api-image-models");
    expect(imageDatalist?.querySelector('option[value="gemini-2.5-flash-image"]')).not.toBeNull();
    expect(imageDatalist?.querySelector('option[value="gemini-2.5-flash"]')).toBeNull();
  });

  it("LlamaCpp scanned-model dropdown appears after a scan and staging a pick updates the model path", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));

    // No scan yet -> no scanned-model dropdown.
    expect(screen.queryByLabelText("Scanned Chat Model")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "System Scan" }));

    // The scan published models, so the dropdown now exists and is selectable.
    const scannedSelect = await screen.findByLabelText("Scanned Chat Model");
    await user.selectOptions(scannedSelect, "/models/chat.gguf");

    await waitFor(() => expect(screen.getByText("/models/chat.gguf")).toBeInTheDocument());
  });

  it("Ollama renders the reasoning mode radios and all 5 task fields defaulting to auto/inherit", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));

    expect(screen.getByRole("radio", { name: "Thinking Mode (Enable CoT)" })).toBeChecked();
    expect(screen.getByLabelText("Chat Model")).toHaveValue("auto");
    expect(screen.getByLabelText("Chat Naming Model")).toHaveValue("inherit");
  });

  it("switching reasoning mode calls through to the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));

    await user.click(screen.getByRole("radio", { name: "Quick Mode (No CoT)" }));

    expect(screen.getByRole("radio", { name: "Quick Mode (No CoT)" })).toBeChecked();
  });

  it("choosing Custom for a task field reveals a text input, and typing sets an explicit assignment", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));

    await user.selectOptions(screen.getByLabelText("Chart Generation Model"), "explicit");
    const chartInput = screen.getByLabelText("Chart Generation Model (custom model ID)");

    await user.type(chartInput, "llama3:8b");

    expect(chartInput).toHaveValue("llama3:8b");
  });

  it("System Scan updates the scan summary and scanned models via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));

    await user.click(screen.getByRole("button", { name: "System Scan" }));

    await waitFor(() =>
      expect(screen.getByText("Using saved system scan results from local Ollama locations.")).toBeInTheDocument(),
    );
  });

  it("Validate and Pull Model is disabled until a model name is typed", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));

    expect(screen.getByRole("button", { name: "Validate and Pull Model" })).toBeDisabled();

    await user.type(screen.getByLabelText("Validate and Pull Model"), "llama3:8b");

    expect(screen.getByRole("button", { name: "Validate and Pull Model" })).toBeEnabled();
  });

  it("LlamaCpp renders the reasoning mode radios and the staged-path placeholders", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));

    expect(screen.getByRole("radio", { name: "Thinking Mode (Enable CoT)" })).toBeChecked();
    expect(screen.getByText("No file selected")).toBeInTheDocument();
    expect(screen.getByText("Reusing the main chat model")).toBeInTheDocument();
    expect(screen.getByText("No model selected")).toBeInTheDocument();
  });

  it("switching LlamaCpp reasoning mode updates the checked radio via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));

    await user.click(screen.getByRole("radio", { name: "Quick Mode (No CoT)" }));

    expect(screen.getByRole("radio", { name: "Quick Mode (No CoT)" })).toBeChecked();
  });

  it("typing a Chat Format Override updates the field via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));

    await user.type(screen.getByLabelText("Chat Format Override"), "chatml");

    expect(screen.getByLabelText("Chat Format Override")).toHaveValue("chatml");
  });

  it("LlamaCpp System Scan updates the scan summary via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));

    await user.click(screen.getByRole("button", { name: "System Scan" }));

    await waitFor(() =>
      expect(screen.getByText("Using saved system scan results from common local model folders.")).toBeInTheDocument(),
    );
  });

  it("Save Settings on LlamaCpp with no staged path reports the empty-path notice via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Llama.cpp (Local)" }));

    await user.click(screen.getByRole("button", { name: "Save Settings" }));

    await waitFor(() => expect(screen.getByText("Chat Model File cannot be empty.")).toBeInTheDocument());
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setActiveSection: vi.fn(),
    setTheme: vi.fn(),
    setShowTokenCounter: vi.fn(),
    setEnableSystemPrompt: vi.fn(),
    setNotificationPreference: vi.fn(),
    setUpdateNotificationsEnabled: vi.fn(),
    checkForUpdates: vi.fn(),
    openRepository: vi.fn(),
    setGithubToken: vi.fn(),
    clearGithubToken: vi.fn(),
    setApiProvider: vi.fn(),
    saveApiConfiguration: vi.fn(),
    loadAvailableModels: vi.fn(),
    resetApiSettings: vi.fn(),
    setOllamaReasoningMode: vi.fn(),
    setOllamaModelAssignment: vi.fn(),
    scanOllamaSystem: vi.fn(),
    pickOllamaScanFolder: vi.fn(),
    pullOllamaModel: vi.fn(),
    setLlamaCppReasoningMode: vi.fn(),
    setLlamaCppChatFormat: vi.fn(),
    setLlamaCppNCtx: vi.fn(),
    setLlamaCppNGpuLayers: vi.fn(),
    setLlamaCppNThreads: vi.fn(),
    pickLlamaCppChatModelFile: vi.fn(),
    pickLlamaCppTitleModelFile: vi.fn(),
    setLlamaCppChatModelPath: vi.fn(),
    setLlamaCppTitleModelPath: vi.fn(),
    scanLlamaCppSystem: vi.fn(),
    pickLlamaCppScanFolder: vi.fn(),
    saveLlamaCppSettings: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { settingsBridge: remote } });
    }
  }
  const qtWindow = window as unknown as QtWindow;
  qtWindow.QWebChannel = FakeQWebChannel as unknown as QtWindow["QWebChannel"];
  qtWindow.qt = { webChannelTransport: {} };
  return remote;
}

function uninstall() {
  const qtWindow = window as unknown as QtWindow;
  delete qtWindow.QWebChannel;
  delete qtWindow.qt;
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders the section Python publishes as active", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialSettingsState, activeSection: "API Endpoint", revision: 1 }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "API Endpoint" })).toHaveAttribute("aria-current", "page"),
    );
  });

  it("clicking a rail button calls through to setActiveSection", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));

    expect(remote.setActiveSection).toHaveBeenCalledWith("Ollama (Local)");
  });

  it("toggling a General/Appearance checkbox calls through to the remote", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("checkbox", { name: "Show Token Counter Overlay" }));

    expect(remote.setShowTokenCounter).toHaveBeenCalledWith(false);
  });

  it("Check for Updates on General calls through to checkForUpdates", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Check for Updates" }));

    expect(remote.checkForUpdates).toHaveBeenCalledTimes(1);
  });

  it("Open Repository on General calls through to openRepository", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Open Repository" }));

    expect(remote.openRepository).toHaveBeenCalledTimes(1);
  });

  it("saving on Integrations calls through to setGithubToken with the typed value", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    // The fake remote's setActiveSection is a bare vi.fn() - it doesn't push
    // a new state back the way real Python would, so the rendered page only
    // actually changes once Python's stateChanged is simulated explicitly.
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialSettingsState, activeSection: "Integrations", revision: 1 }));
    await waitFor(() => expect(screen.getByLabelText("GitHub Personal Access Token")).toBeInTheDocument());

    await user.type(screen.getByLabelText("GitHub Personal Access Token"), "ghp_typed");
    await user.click(screen.getByRole("button", { name: "Save Integrations" }));

    expect(remote.setGithubToken).toHaveBeenCalledWith("ghp_typed");
  });

  it("Clear Token on Integrations calls through to clearGithubToken", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialSettingsState, activeSection: "Integrations", revision: 1 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Clear Token" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Clear Token" }));

    expect(remote.clearGithubToken).toHaveBeenCalledTimes(1);
  });

  it("changing the API provider select calls through to setApiProvider", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialSettingsState, activeSection: "API Endpoint", revision: 1 }));
    await waitFor(() => expect(screen.getByLabelText("API Provider")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("API Provider"), "Google Gemini");

    expect(remote.setApiProvider).toHaveBeenCalledWith("Google Gemini");
  });

  it("Save Configuration calls through to saveApiConfiguration with the typed values as JSON", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialSettingsState, activeSection: "API Endpoint", revision: 1 }));
    await waitFor(() => expect(screen.getByLabelText("API Key")).toBeInTheDocument());

    await user.type(screen.getByLabelText("API Key"), "sk-typed");
    await user.click(screen.getByRole("button", { name: "Save Configuration" }));

    expect(remote.saveApiConfiguration).toHaveBeenCalledWith(
      expect.stringContaining("sk-typed"),
    );
  });

  it("System Scan on Ollama calls through to scanOllamaSystem", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialSettingsState, activeSection: "Ollama (Local)", revision: 1 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "System Scan" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "System Scan" }));

    expect(remote.scanOllamaSystem).toHaveBeenCalledTimes(1);
  });

  it("Browse for Chat Model File on LlamaCpp calls through to pickLlamaCppChatModelFile", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialSettingsState, activeSection: "Llama.cpp (Local)", revision: 1 }));
    await waitFor(() => expect(screen.getByText("Chat Model File")).toBeInTheDocument());

    await user.click(screen.getAllByRole("button", { name: "Browse..." })[0]);

    expect(remote.pickLlamaCppChatModelFile).toHaveBeenCalledTimes(1);
  });

  it("Save Settings on LlamaCpp calls through to saveLlamaCppSettings", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialSettingsState, activeSection: "Llama.cpp (Local)", revision: 1 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save Settings" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Save Settings" }));

    expect(remote.saveLlamaCppSettings).toHaveBeenCalledTimes(1);
  });

  // Confirms App.tsx actually reaches the shared lib/ui/BridgeErrorState on
  // a rejected payload, with this island's own title/className - the
  // shared component's own rendering logic is covered by
  // lib/ui/BridgeErrorState.test.tsx, this only proves the wiring at this
  // specific call site is correct.
  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialSettingsState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Settings unavailable")).toBeInTheDocument();
  });
});
