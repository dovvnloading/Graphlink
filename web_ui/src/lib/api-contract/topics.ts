/* GENERATED - do not hand-edit. Source of truth: contracts/codegen.py::GENERATED_ARTIFACTS.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */


import { type AppAboutState, validateAppAboutState } from "../bridge-core/generated/app-about-state";
import { type AppChatLibraryState, validateAppChatLibraryState } from "../bridge-core/generated/app-chat-library-state";
import { type AppComposerState, validateAppComposerState } from "../bridge-core/generated/app-composer-state";
import { type AppPluginsState, validateAppPluginsState } from "../bridge-core/generated/app-plugins-state";
import { type AppSettingsState, validateAppSettingsState } from "../bridge-core/generated/app-settings-state";
import { type DragSpeedState, validateDragSpeedState } from "../bridge-core/generated/drag-speed-state";
import { type ExecutionLimitsState, validateExecutionLimitsState } from "../bridge-core/generated/execution-limits-state";
import { type FontControlState, validateFontControlState } from "../bridge-core/generated/font-control-state";
import { type GridControlState, validateGridControlState } from "../bridge-core/generated/grid-control-state";
import { type NotificationState, validateNotificationState } from "../bridge-core/generated/notification-state";
import { type SceneState, validateSceneState } from "../bridge-core/generated/scene-state";
import { type TokenCounterState, validateTokenCounterState } from "../bridge-core/generated/token-counter-state";

export const TOPIC_VALIDATORS = {
  "app-about": validateAppAboutState,
  "app-chat-library": validateAppChatLibraryState,
  "app-composer": validateAppComposerState,
  "app-plugins": validateAppPluginsState,
  "app-settings": validateAppSettingsState,
  "drag-speed": validateDragSpeedState,
  "execution-limits": validateExecutionLimitsState,
  "font-control": validateFontControlState,
  "grid-control": validateGridControlState,
  "notification": validateNotificationState,
  "scene": validateSceneState,
  "token-counter": validateTokenCounterState,
} as const;

export type TopicName = keyof typeof TOPIC_VALIDATORS;

export interface TopicStates {
  "app-about": AppAboutState;
  "app-chat-library": AppChatLibraryState;
  "app-composer": AppComposerState;
  "app-plugins": AppPluginsState;
  "app-settings": AppSettingsState;
  "drag-speed": DragSpeedState;
  "execution-limits": ExecutionLimitsState;
  "font-control": FontControlState;
  "grid-control": GridControlState;
  "notification": NotificationState;
  "scene": SceneState;
  "token-counter": TokenCounterState;
}
