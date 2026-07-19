/**
 * The composer island's state contract.
 *
 * The TYPES here are re-exported from lib/bridge-core/generated/composer-state.ts,
 * which is generated from graphlink_app/graphlink_composer_payload.py - the
 * Python dataclasses that define what the desktop side actually sends. They are
 * deliberately NOT declared here anymore: this file previously hand-mirrored
 * every interface, which is exactly the drift risk the schema pipeline exists
 * to remove (a Python-side field rename would silently disagree with a mirror
 * nobody remembered to update, and nothing would fail).
 *
 * Re-exporting rather than pointing every consumer at the generated path keeps
 * `import { ComposerState } from "./bridgeTypes"` working across the island's
 * existing code and tests, so the generated file stays an implementation
 * detail of where the contract comes from.
 *
 * What legitimately still lives here: `initialComposerState`, the mock snapshot
 * used for browser-preview/dev and by the jsdom tests. That is fixture DATA,
 * not contract shape - and it is type-checked against the generated
 * `ComposerState`, so it cannot drift from the contract without failing the
 * build.
 */
export type {
  ComposerAttachment,
  ComposerCapabilities,
  ComposerContext,
  ComposerContextAnchor,
  ComposerDraft,
  ComposerModelOption,
  ComposerReasoning,
  ComposerReasoningOption,
  ComposerRequest,
  ComposerRoute,
  ComposerState,
  ComposerTheme,
  ComposerThemeGraphNode,
  ComposerThemeNeutralButton,
  ComposerThemePalette,
  ComposerThemeSemantic,
} from "../../lib/bridge-core/generated/composer-state";

import type { ComposerState } from "../../lib/bridge-core/generated/composer-state";

/**
 * RequestState is not a named export of the generated module - the generator
 * inlines string-literal unions at their use site rather than hoisting them to
 * named aliases. Deriving it from the generated ComposerRequest keeps it tied
 * to the generated contract instead of restating the union by hand.
 */
export type RequestState = ComposerState["request"]["state"];

export const initialComposerState: ComposerState = {
  schemaVersion: 1,
  // Mirrors what IslandBridge.publish() actually stamps onto every real
  // payload, so this fixture stays a faithful stand-in for one rather than a
  // subtly different shape that only the mock path ever sees.
  minCompatibleSchemaVersion: 1,
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
