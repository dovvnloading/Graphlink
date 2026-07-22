/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_island_codegen.py::GENERATED_ARTIFACTS.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */


import { type AboutState, validateAboutState } from "../bridge-core/generated/about-state";
import { type AppComposerState, validateAppComposerState } from "../bridge-core/generated/app-composer-state";
import { type ChatLibraryState, validateChatLibraryState } from "../bridge-core/generated/chat-library-state";
import { type CommandPaletteState, validateCommandPaletteState } from "../bridge-core/generated/command-palette-state";
import { type ComposerState, validateComposerState } from "../bridge-core/generated/composer-state";
import { type ComposerContextState, validateComposerContextState } from "../bridge-core/generated/composer-context-state";
import { type ComposerPickerState, validateComposerPickerState } from "../bridge-core/generated/composer-picker-state";
import { type DocumentViewerState, validateDocumentViewerState } from "../bridge-core/generated/document-viewer-state";
import { type DragSpeedState, validateDragSpeedState } from "../bridge-core/generated/drag-speed-state";
import { type FontControlState, validateFontControlState } from "../bridge-core/generated/font-control-state";
import { type GridControlState, validateGridControlState } from "../bridge-core/generated/grid-control-state";
import { type HelpState, validateHelpState } from "../bridge-core/generated/help-state";
import { type MinimapState, validateMinimapState } from "../bridge-core/generated/minimap-state";
import { type NotificationState, validateNotificationState } from "../bridge-core/generated/notification-state";
import { type PinOverlayState, validatePinOverlayState } from "../bridge-core/generated/pin-overlay-state";
import { type PluginPickerState, validatePluginPickerState } from "../bridge-core/generated/plugin-picker-state";
import { type SceneState, validateSceneState } from "../bridge-core/generated/scene-state";
import { type SearchOverlayState, validateSearchOverlayState } from "../bridge-core/generated/search-overlay-state";
import { type SettingsState, validateSettingsState } from "../bridge-core/generated/settings-state";
import { type TokenCounterState, validateTokenCounterState } from "../bridge-core/generated/token-counter-state";
import { type ToolbarState, validateToolbarState } from "../bridge-core/generated/toolbar-state";

export const TOPIC_VALIDATORS = {
  "about": validateAboutState,
  "app-composer": validateAppComposerState,
  "chat-library": validateChatLibraryState,
  "command-palette": validateCommandPaletteState,
  "composer": validateComposerState,
  "composer-context": validateComposerContextState,
  "composer-picker": validateComposerPickerState,
  "document-viewer": validateDocumentViewerState,
  "drag-speed": validateDragSpeedState,
  "font-control": validateFontControlState,
  "grid-control": validateGridControlState,
  "help": validateHelpState,
  "minimap": validateMinimapState,
  "notification": validateNotificationState,
  "pin-overlay": validatePinOverlayState,
  "plugin-picker": validatePluginPickerState,
  "scene": validateSceneState,
  "search-overlay": validateSearchOverlayState,
  "settings": validateSettingsState,
  "token-counter": validateTokenCounterState,
  "toolbar": validateToolbarState,
} as const;

export type TopicName = keyof typeof TOPIC_VALIDATORS;

export interface TopicStates {
  "about": AboutState;
  "app-composer": AppComposerState;
  "chat-library": ChatLibraryState;
  "command-palette": CommandPaletteState;
  "composer": ComposerState;
  "composer-context": ComposerContextState;
  "composer-picker": ComposerPickerState;
  "document-viewer": DocumentViewerState;
  "drag-speed": DragSpeedState;
  "font-control": FontControlState;
  "grid-control": GridControlState;
  "help": HelpState;
  "minimap": MinimapState;
  "notification": NotificationState;
  "pin-overlay": PinOverlayState;
  "plugin-picker": PluginPickerState;
  "scene": SceneState;
  "search-overlay": SearchOverlayState;
  "settings": SettingsState;
  "token-counter": TokenCounterState;
  "toolbar": ToolbarState;
}
