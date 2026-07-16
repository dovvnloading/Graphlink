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
  label: string;
  available: boolean;
  canChange: boolean;
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
    routeSelection: false,
    cancellation: true,
  },
  theme: { mode: "dark", accent: "#83a7ff", surface: "#1b1f25" },
};
