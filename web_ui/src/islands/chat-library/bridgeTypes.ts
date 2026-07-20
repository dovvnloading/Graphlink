/**
 * The chat-library island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). What legitimately still lives here:
 * initialChatLibraryState, the mock snapshot used for browser-preview/dev
 * and by jsdom tests.
 */
export type { ChatLibraryState, ChatLibraryRow } from "../../lib/bridge-core/generated/chat-library-state";

import type { ChatLibraryState } from "../../lib/bridge-core/generated/chat-library-state";

export const initialChatLibraryState: ChatLibraryState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  rows: [],
  notice: null,
};
