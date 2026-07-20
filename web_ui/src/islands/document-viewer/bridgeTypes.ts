/**
 * The document-viewer island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). Unlike help/bridgeTypes.ts, `content`
 * IS a real Python-side field here: the markdown text
 * _extract_document_view_content() (graphlink_window.py) produced for
 * whichever node the user last opened.
 */
export type { DocumentViewerState } from "../../lib/bridge-core/generated/document-viewer-state";

import type { DocumentViewerState } from "../../lib/bridge-core/generated/document-viewer-state";

export const initialDocumentViewerState: DocumentViewerState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  content: "",
};
