/**
 * Shared topic-subscribe-and-validate glue for the client stores (SceneStore,
 * ComposerStore).
 *
 * Factored out because both stores independently declared a private `bind`
 * method with byte-for-byte identical bodies: subscribe on the transport,
 * run the payload through TOPIC_VALIDATORS[topic], and on success call the
 * caller's `assign` then `emit`; on failure, console.error the topic name and
 * the validation errors. Each store still owns its own `transport` field and
 * `emit()` method - this just takes both as parameters instead of closing
 * over `this`.
 */

import type { TopicName } from "./topics";
import { validateAppComposerState } from "../bridge-core/generated/app-composer-state";
import { validateDragSpeedState } from "../bridge-core/generated/drag-speed-state";
import { validateFontControlState } from "../bridge-core/generated/font-control-state";
import { validateGridControlState } from "../bridge-core/generated/grid-control-state";
import { validateNotificationState } from "../bridge-core/generated/notification-state";
import { validateSceneState } from "../bridge-core/generated/scene-state";
import { validateTokenCounterState } from "../bridge-core/generated/token-counter-state";

// Only the live canvas/composer subscriptions belong in the first-paint
// validator table. Chrome-only topics are validated in their lazy surfaces;
// importing the generated all-topics table here pulled every dormant dialog's
// validator into the main JS chunk even when that dialog never opened.
const LIVE_TOPIC_VALIDATORS = {
  "app-composer": validateAppComposerState,
  "drag-speed": validateDragSpeedState,
  "font-control": validateFontControlState,
  "grid-control": validateGridControlState,
  "notification": validateNotificationState,
  "scene": validateSceneState,
  "token-counter": validateTokenCounterState,
} satisfies Partial<Record<TopicName, (value: unknown) => { ok: boolean }>>;

export type LiveTopicName = keyof typeof LIVE_TOPIC_VALIDATORS;
import type { StateListener } from "../ws/transport";

/** The one transport capability bindTopic needs: enough to attach a
 * per-topic listener and get an unsubscribe function back. WsTransport
 * satisfies this structurally. */
export interface TopicSubscribable {
  subscribe(topic: string, listener: StateListener): () => void;
}

/** Subscribe to `topic` on `transport`. Every payload is validated against
 * TOPIC_VALIDATORS[topic] before use: a valid payload is handed to `assign`
 * and followed by `emit()`; an invalid one is logged and dropped, leaving
 * whatever the caller already had untouched. Returns the unsubscribe
 * function `transport.subscribe` produced. */
export function bindTopic<T>(
  transport: TopicSubscribable,
  topic: LiveTopicName,
  assign: (value: T) => void,
  emit: () => void,
): () => void {
  return transport.subscribe(topic, (payload) => {
    const validated = LIVE_TOPIC_VALIDATORS[topic](payload);
    if (validated.ok) {
      assign(validated.value as T);
      emit();
    } else {
      console.error(`[${topic}] rejected snapshot:`, validated.errors);
    }
  });
}
