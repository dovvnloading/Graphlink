import { vi } from "vitest";
import type { WsTransport } from "./transport";

/**
 * Shared fake for dialogs that only ever call transport.request() (never
 * fireIntent/intent) - subscribe/intent/fireIntent are no-ops. Extracted
 * from BuilderLaunchDialog.test.tsx, GlobalSearchDialog.test.tsx, and
 * KnowledgeSearchDialog.test.tsx, which each defined this exact shape
 * independently (their own comments already called out matching one
 * another "exactly" - this makes that explicit instead of leaving three
 * copies to drift).
 *
 * request() both records into `intents` (for call-shape assertions) and
 * forwards to the returned `request` vi.fn(), so a test can drive its
 * resolution/rejection with mockResolvedValueOnce/mockRejectedValueOnce/
 * mockImplementation.
 */
export function makeRequestOnlyTransport() {
  const intents: unknown[][] = [];
  const request = vi.fn<(topic: string, intent: string, args?: unknown[]) => Promise<unknown>>();
  const transport = {
    subscribe: () => () => {},
    intent: () => {},
    fireIntent: () => {},
    request: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
      return request(topic, intent, args);
    },
  } as unknown as WsTransport;
  return { transport, intents, request };
}
