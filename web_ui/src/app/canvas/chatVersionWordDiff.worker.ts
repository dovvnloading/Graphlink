import { diffChatVersionWords, type ChatWordDiff } from "./chatVersionWordDiff";

// Dedicated worker: even an expensive rewrite never blocks canvas rendering,
// focus, selection or scrolling on the main UI thread. The worker-shaped cast
// avoids importing lib.webworker alongside the app's DOM lib, which otherwise
// declares overlapping browser globals during tsc's single-project check.
const worker = self as unknown as {
  onmessage: ((event: MessageEvent<{ left: string; right: string }>) => void) | null;
  postMessage: (diff: ChatWordDiff) => void;
};

worker.onmessage = (event) => {
  const { left, right } = event.data;
  worker.postMessage(diffChatVersionWords(left, right));
};
