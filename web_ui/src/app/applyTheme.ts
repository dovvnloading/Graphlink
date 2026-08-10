/**
 * ADR-012 stage 12.2: stamps (or clears) [data-theme] on <html>, the
 * attribute gl-vars-dev.css's own cascade reads (see that file's own header
 * for the full 3-layer explanation). Its own module rather than an export
 * from App.tsx for the same reason as connectionBadge.ts - keeps
 * react-refresh/only-export-components happy and lets a test pin the DOM
 * side effect without standing up ReactFlow, the overlay provider and a
 * live WsTransport.
 *
 * "system" deliberately REMOVES the attribute rather than setting it to some
 * third value - the CSS has no :root[data-theme="system"] rule at all, only
 * a real light/dark choice or prefers-color-scheme's own media query, which
 * only ever fires for an element with NO explicit data-theme.
 */
export function applyTheme(theme: string): void {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") {
    root.setAttribute("data-theme", theme);
  } else {
    root.removeAttribute("data-theme");
  }
}
