/**
 * The notification island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). What legitimately still lives here:
 * initialNotificationState, the mock snapshot used for browser-preview/dev
 * and by jsdom tests.
 */
export type { NotificationState } from "../../lib/bridge-core/generated/notification-state";

import type { NotificationState } from "../../lib/bridge-core/generated/notification-state";

export const initialNotificationState: NotificationState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  visible: false,
  message: "",
  msgType: "info",
};
