export type RequestState =
  | "idle"
  | "preparing"
  | "uploading"
  | "waiting"
  | "generating"
  | "finalizing"
  | "canceled"
  | "failed"
  | "succeeded";

export interface ComposerAttachment {
  id: string;
  name: string;
  kind: string;
  tokenCount: number;
  preparationState: string;
  contextLabel?: string;
}

export interface ComposerContext {
  anchor: { id: string; label: string; type: string } | null;
  items: ComposerAttachment[];
  totalTokens: number;
  reviewAvailable: boolean;
}

export interface ComposerRoute {
  mode: "cloud" | "ollama" | "llamacpp" | "unknown";
  provider: string;
  modelId: string;
  modelValue?: string;
  modelLabel: string;
  modelOptions: ComposerModelOption[];
  reasoning: ComposerReasoning;
  label: string;
  available: boolean;
  canChange: boolean;
}

export interface ComposerModelOption {
  id: string;
  label: string;
  provider: string;
  source: "installed" | "catalog" | "saved" | "configured" | string;
  active: boolean;
  ready: boolean;
  available: boolean;
  capabilities: string[];
}

export interface ComposerReasoningOption {
  id: "Quick" | "Thinking" | string;
  label: string;
  description: string;
}

export interface ComposerReasoning {
  level: "Quick" | "Thinking" | string;
  label: string;
  options: ComposerReasoningOption[];
}

export interface ComposerState {
  schemaVersion: 1;
  revision: number;
  draft: {
    id: string;
    text: string;
    contextMode: string;
    sendMode: "enter_to_send" | "ctrl_enter_to_send";
    restored: boolean;
  };
  context: ComposerContext;
  route: ComposerRoute;
  request: {
    id: string | null;
    state: RequestState;
    message: string;
    canSend: boolean;
    canCancel: boolean;
    canRetry: boolean;
  };
  capabilities: {
    attachments: boolean;
    contextReview: boolean;
    routeSelection: boolean;
    modelSelection: boolean;
    reasoningSelection: boolean;
    settingsShortcut: boolean;
    cancellation: boolean;
  };
  theme: { mode: "dark" | "light"; accent: string; surface: string };
}

export const initialComposerState: ComposerState = {
  schemaVersion: 1,
  revision: 0,
  draft: {
    id: "browser-preview",
    text: "",
    contextMode: "branch",
    sendMode: "enter_to_send",
    restored: false,
  },
  context: {
    anchor: null,
    items: [],
    totalTokens: 0,
    reviewAvailable: false,
  },
  route: {
    mode: "ollama",
    provider: "Ollama",
    modelId: "",
    modelLabel: "Select a model",
    modelOptions: [],
    reasoning: {
      level: "Thinking",
      label: "Thinking",
      options: [
        { id: "Quick", label: "Quick", description: "Direct responses with less deliberation." },
        { id: "Thinking", label: "Thinking", description: "More deliberate reasoning for complex requests." },
      ],
    },
    label: "Local · Ollama",
    available: true,
    canChange: false,
  },
  request: {
    id: null,
    state: "idle",
    message: "",
    canSend: false,
    canCancel: false,
    canRetry: false,
  },
  capabilities: {
    attachments: true,
    contextReview: true,
    routeSelection: true,
    modelSelection: true,
    reasoningSelection: true,
    settingsShortcut: true,
    cancellation: true,
  },
  theme: { mode: "dark", accent: "#a0a0a0", surface: "#1d1d1d" },
};
