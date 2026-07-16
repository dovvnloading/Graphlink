import { ComposerState } from "./bridgeTypes";

export function contextCount(state: ComposerState): number {
  return state.context.items.length + (state.context.anchor ? 1 : 0);
}

export function contextSummary(state: ComposerState): string {
  const count = contextCount(state);
  if (!count) return "No context attached";
  if (count === 1) return "1 context item";
  return count + " context items";
}

export function requestLabel(state: ComposerState): string {
  if (state.request.state === "idle") return "";
  return state.request.message || state.request.state;
}

export function isBusy(state: ComposerState): boolean {
  return ["preparing", "uploading", "waiting", "generating", "finalizing"].includes(
    state.request.state,
  );
}
