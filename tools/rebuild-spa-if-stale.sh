#!/usr/bin/env bash
# Rebuild the SPA bundle whenever it is older than the source it is built
# from.
#
# WHY THIS EXISTS. The app does not run the source tree. Both the desktop
# shell (graphlink_desktop.py) and the FastAPI backend (backend/app.py,
# SPA_DIST_DIR) serve web_ui/dist/app - a Vite build. `npm run dev` on :5173
# reads source live, so it is entirely possible to change the UI, verify it
# in the dev server, and have the real application go on serving a bundle
# built days earlier. That happened: the toolbar and Builder rebuilds were
# verified on :5173 while the app served an eleven-day-old bundle, and the
# work looked like it had never landed.
#
# dist/ is gitignored, so CI cannot catch this - it is a local-state problem
# and needs a local guard. This runs from a Stop hook (.claude/settings.json)
# and rebuilds only when the bundle is actually behind, so a turn that
# touched no frontend source costs one `find`.
#
# It deliberately does NOT gate on git state. The bundle's job is to reflect
# the working tree, which is what the developer is looking at.

set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$root/web_ui/src"
stamp="$root/web_ui/dist/app/index.html"

# Nothing to do outside a checkout that has the frontend.
[ -d "$src" ] || exit 0

emit() { printf '{"systemMessage":%s}\n' "$1"; }

# Inputs that change what the bundle contains. index.html lives under
# src/app/, so it is already covered by the tree walk.
newest="$(
  find "$src" -type f -newer "$stamp" -print -quit 2>/dev/null
)"
if [ ! -f "$stamp" ]; then
  newest="(no bundle yet)"
elif [ -z "$newest" ]; then
  for f in "$root/web_ui/vite.config.ts" "$root/web_ui/package.json"; do
    [ -f "$f" ] && [ "$f" -nt "$stamp" ] && newest="$f" && break
  done
fi

[ -n "$newest" ] || exit 0

if (cd "$root/web_ui" && npm run build >/tmp/graphlink-spa-build.log 2>&1); then
  emit '"web_ui/dist/app rebuilt - the desktop app and :8765 now serve the current source."'
else
  emit '"web_ui/dist/app is STALE: npm run build failed. See /tmp/graphlink-spa-build.log. The desktop app and :8765 are still serving the previous bundle."'
fi
exit 0
