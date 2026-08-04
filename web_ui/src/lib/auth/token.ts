/**
 * ADR-004 stage 4.1: the client half of the per-launch capability token.
 *
 * graphlink_desktop.py mints a 256-bit token per launch and opens the window
 * at `http://127.0.0.1:<port>/#token=<token>`. Every /api/* request and the
 * /ws handshake must carry it back, or the backend answers 401 / closes the
 * socket - see backend/auth.py's own docstring for the threat this closes
 * (audit C5: any other local process can otherwise drive all 131 intents,
 * including the approve-code-execution gate itself).
 *
 * WHY THE FRAGMENT IS READ ON EVERY CALL rather than cached at module load:
 * a property read plus a short string split is free next to the network
 * request it is about to authenticate, and not caching removes a whole class
 * of bug (a stale cache after any navigation, and cross-test leakage in
 * vitest where the module registry is shared but `window.location` is not).
 *
 * WHY THE FRAGMENT IS NOT STRIPPED after reading. Stripping it (via
 * history.replaceState) would hide the token from devtools, but it would
 * also mean any page reload - Ctrl+R in a debug window, or a programmatic
 * location.reload() - permanently loses the token and bricks the window
 * until the app is relaunched. In a pywebview window there is no address bar
 * to leak it to and no other origin to leak it at, so keeping it is the
 * clearly better trade. Fragments are never sent to the server, so this
 * still keeps the token out of access logs and Referer headers, which is the
 * property that actually matters.
 *
 * Every function here degrades to a no-op when there is no token in the URL.
 * That is the normal case for two legitimate callers: the vitest suite (no
 * fragment, and the backend under test has auth disabled), and a developer
 * running the vite dev server, who instead sets GRAPHLINK_DEV_AUTH_TOKEN on
 * the backend and appends the same value to the URL by hand.
 */

const TOKEN_FRAGMENT_KEY = "token";

/**
 * The capability token from the URL fragment, or null when there isn't one.
 *
 * Parsed with URLSearchParams over the fragment body so a future second
 * fragment key cannot break this, and so percent-encoding is handled for
 * free - though `secrets.token_urlsafe` (backend/auth.py) only ever produces
 * characters that need no escaping.
 */
export function getAuthToken(): string | null {
  if (typeof window === "undefined" || !window.location) return null;
  const hash = window.location.hash;
  if (!hash || hash.length < 2) return null;
  const params = new URLSearchParams(hash.slice(1));
  const token = params.get(TOKEN_FRAGMENT_KEY);
  return token ? token : null;
}

/**
 * Append the token to a URL as a `token=` query parameter.
 *
 * The query-parameter form (rather than an Authorization header) is
 * mandatory for the asset endpoints: `<img src="/api/assets/...">` is loaded
 * by the browser's own image loader, which cannot be given headers. Using
 * the SAME builder for the fetch() call sites too - rather than headers
 * there and a query param here - keeps one URL shape per endpoint, so the
 * <img> tag and the fetch of the same asset can never diverge.
 *
 * Picks the separator from whether the URL already has a query string, so it
 * composes correctly with existing params (e.g. chart's `?v=<version>`
 * cache-buster).
 */
export function withAuthToken(url: string): string {
  const token = getAuthToken();
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}${TOKEN_FRAGMENT_KEY}=${encodeURIComponent(token)}`;
}

/**
 * `Authorization: Bearer <token>` headers for a fetch(), or an empty object
 * when there is no token - so a call site can always spread this into its
 * RequestInit without branching.
 *
 * Not used by the asset paths (see withAuthToken above on why those carry
 * the token in the URL instead); provided for any future /api call made with
 * fetch() where the header form is the more natural fit.
 */
export function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
