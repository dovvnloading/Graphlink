import { ComposerState } from "./bridgeTypes";

export function requestLabel(state: ComposerState): string {
  if (state.request.state === "idle") return "";
  return state.request.message || state.request.state;
}

export function isBusy(state: ComposerState): boolean {
  return ["preparing", "uploading", "waiting", "generating", "finalizing"].includes(
    state.request.state,
  );
}
