/**
 * The composer-context island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). `anchor`/`items`/`totalTokens` are
 * forwarded verbatim from ComposerBridge's own context dict - see
 * graphlink_composer_context_payload.py for the full rationale.
 */
export type {
  ComposerContextState,
  ComposerContextAnchor,
  ComposerContextItem,
} from "../../lib/bridge-core/generated/composer-context-state";

import type { ComposerContextState } from "../../lib/bridge-core/generated/composer-context-state";

export const initialComposerContextState: ComposerContextState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  items: [],
  totalTokens: 0,
  anchor: null,
};
