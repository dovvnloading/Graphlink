/**
 * Generic QWebChannel connection bootstrapping, shared by every island's
 * bridge.ts. Feature-detection and channel construction are identical
 * across islands (this exact shape, extracted from composer/bridge.ts);
 * only the registered object name and its typed remote interface differ
 * per island, and stay in that island's own bridge.ts.
 */

export interface QtWindow extends Window {
  qt?: { webChannelTransport?: unknown };
  QWebChannel?: new (
    transport: unknown,
    callback: (channel: { objects: Record<string, unknown> }) => void,
  ) => unknown;
}

export function isQWebChannelAvailable(win: Window = window): boolean {
  const qtWindow = win as QtWindow;
  return Boolean(qtWindow.QWebChannel && qtWindow.qt?.webChannelTransport);
}

/**
 * Connects to the Qt-side QWebChannel and hands back every registered
 * object, keyed by the name Python registered it under. A no-op (never
 * calls onConnected) when QWebChannel isn't available.
 *
 * Every current caller already gates on isQWebChannelAvailable() before
 * calling this, making the guard below redundant today - kept anyway so
 * this function is safe to call standalone (its own contract holds without
 * relying on caller discipline this file can't enforce), for whichever
 * island's bridge.ts is the first to call it without checking first.
 */
export function connectQWebChannel(
  onConnected: (objects: Record<string, unknown>) => void,
  win: Window = window,
): void {
  const qtWindow = win as QtWindow;
  if (!qtWindow.QWebChannel || !qtWindow.qt?.webChannelTransport) return;
  new qtWindow.QWebChannel(qtWindow.qt.webChannelTransport, (channel) => {
    onConnected(channel.objects);
  });
}
