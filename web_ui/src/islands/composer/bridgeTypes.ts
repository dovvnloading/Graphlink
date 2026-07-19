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
  theme: ComposerTheme;
}

export interface ComposerThemePalette {
  userNode: string;
  aiNode: string;
  selection: string;
  navHighlight: string;
}

export interface ComposerThemeSemantic {
  searchHighlight: string;
  statusInfo: string;
  statusSuccess: string;
  statusError: string;
  statusWarning: string;
  artifact: string;
  conversationUserBubble: string;
  conversationAiBubble: string;
  default: string;
}

export interface ComposerThemeNeutralButton {
  background: string;
  hover: string;
  pressed: string;
  border: string;
  icon: string;
  mutedIcon: string;
}

export interface ComposerThemeGraphNode {
  border: string;
  header: string;
  dot: string;
  hoverDot: string;
  hoverOutline: string;
  selectedOutline: string;
  bodyStart: string;
  bodyEnd: string;
  headerStart: string;
  headerEnd: string;
  badgeFill: string;
  panelFill: string;
  panelBorder: string;
}

export interface ComposerTheme {
  mode: "dark" | "light";
  name: string;
  // Every --gl-* custom property name/value pair for the active theme,
  // straight from the Python side's css_custom_properties() - the same
  // function the host's build-time :root injection uses, so this can never
  // disagree with what first paint already showed. Applied to
  // document.documentElement on every snapshot; see ComposerApp.tsx.
  cssVariables: Record<string, string>;
  palette: ComposerThemePalette;
  semantic: ComposerThemeSemantic;
  neutralButton: ComposerThemeNeutralButton;
  graphNode: ComposerThemeGraphNode;
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
  theme: {
    mode: "dark",
    name: "dark",
    // Empty in the browser-preview mock: nothing here needs it, since
    // `npm run dev` gets its baseline styling from the separately-imported
    // lib/tokens/gl-vars-dev.css, not from bridge state. The theme-application
    // effect below is a no-op on an empty object.
    cssVariables: {},
    palette: {
      userNode: "#838383",
      aiNode: "#828282",
      selection: "#858585",
      navHighlight: "#949494",
    },
    semantic: {
      searchHighlight: "#949494",
      statusInfo: "#828282",
      statusSuccess: "#838383",
      statusError: "#848484",
      statusWarning: "#919191",
      artifact: "#828282",
      conversationUserBubble: "#696969",
      conversationAiBubble: "#323232",
      default: "#858585",
    },
    neutralButton: {
      background: "#393939",
      hover: "#484848",
      pressed: "#343434",
      border: "#585858",
      icon: "#F0F0F0",
      mutedIcon: "#BDBDBD",
    },
    graphNode: {
      border: "#585858",
      header: "#bdbdbd",
      dot: "#585858",
      hoverDot: "#484848",
      hoverOutline: "#515151",
      selectedOutline: "#595959",
      bodyStart: "#303030",
      bodyEnd: "#292929",
      headerStart: "#3c3c3c",
      headerEnd: "#333333",
      badgeFill: "#484848",
      panelFill: "#202020",
      panelBorder: "#585858",
    },
  },
};
