import type { AppComposerRoute } from "../../lib/bridge-core/generated/app-composer-state";

/**
 * The one rule for "a normal chat message can be sent on this route": the
 * provider is available AND a chat model is assigned. The API route reports
 * available even with no model selected, so availability alone is not
 * enough. Composer's Send and onboarding's readiness both use this (#1131).
 */
export function routeHasUsableModel(route: Pick<AppComposerRoute, "available" | "modelId"> | null): boolean {
  return !!route && route.available && !!route.modelId;
}
