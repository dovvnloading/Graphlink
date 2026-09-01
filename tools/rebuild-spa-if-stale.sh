#!/usr/bin/env bash
# Keep web_ui/dist/app honest: rebuild it whenever it no longer reflects the
# checkout, and say so loudly when the CHECKOUT itself is behind.
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
# and needs a local guard. This runs from a Stop hook (.claude/settings.json).
#
# TWO DIFFERENT STALENESS PROBLEMS, and the first version only solved one:
#
#   1. The bundle is older than the source it is built from. Caught by the
#      mtime walk below, and rebuilt.
#
#   2. The CHECKOUT is behind the branch the work is landing on. Merged work
#      is not in this working tree at all, so the bundle can be perfectly
#      consistent with the source and the running app still misses features
#      that shipped. The mtime walk is silent here BY CONSTRUCTION - there is
#      nothing newer to find - which is exactly how a canvas went on spawning
#      placeholder nodes after the commit removing them had already merged.
#      Reported below, never auto-merged: the working tree is the developer's,
#      and this hook has no business rewriting it.
#
# It deliberately does NOT gate the rebuild on git state. The bundle's job is
# to reflect the working tree, which is what the developer is looking at.

set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$root/web_ui/src"
dist="$root/web_ui/dist/app"
stamp="$dist/index.html"
# Records the commit the bundle was built from. A pull or a branch switch
# that happens to leave mtimes alone is invisible to the walk below; the
# recorded HEAD is not.
head_stamp="$dist/.built-from-commit"

# Nothing to do outside a checkout that has the frontend.
[ -d "$src" ] || exit 0

emit() { printf '{"systemMessage":%s}\n' "$1"; }

head_sha=""
if command -v git >/dev/null 2>&1; then
  head_sha="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
fi

# -- 1. Does the bundle still match the working tree? -----------------------

newest="$(find "$src" -type f -newer "$stamp" -print -quit 2>/dev/null)"
if [ ! -f "$stamp" ]; then
  newest="(no bundle yet)"
elif [ -z "$newest" ]; then
  for f in "$root/web_ui/vite.config.ts" "$root/web_ui/package.json"; do
    [ -f "$f" ] && [ "$f" -nt "$stamp" ] && newest="$f" && break
  done
fi

# A bundle built from a different commit is stale even when every mtime says
# otherwise - see problem 2 in this file's own header.
if [ -z "$newest" ] && [ -n "$head_sha" ]; then
  built_from="$(cat "$head_stamp" 2>/dev/null || true)"
  [ "$built_from" = "$head_sha" ] || newest="(built from a different commit)"
fi

rebuilt=""
if [ -n "$newest" ]; then
  if (cd "$root/web_ui" && npm run build >/tmp/graphlink-spa-build.log 2>&1); then
    [ -n "$head_sha" ] && printf '%s' "$head_sha" > "$head_stamp" 2>/dev/null
    rebuilt="ok"
  else
    emit '"web_ui/dist/app is STALE: npm run build failed. See /tmp/graphlink-spa-build.log. The desktop app and :8765 are still serving the previous bundle."'
    exit 0
  fi
fi

# -- 2. Is the checkout itself behind what has already merged? --------------
#
# Reported, never acted on. Reads only refs already fetched - no network, so
# a Stop hook never blocks on one.

behind=""
if [ -n "$head_sha" ]; then
  upstream=""
  for candidate in origin/main origin/master; do
    if git -C "$root" rev-parse --verify --quiet "$candidate" >/dev/null 2>&1; then
      upstream="$candidate"
      break
    fi
  done
  if [ -n "$upstream" ]; then
    count="$(git -C "$root" rev-list --count "HEAD..$upstream" 2>/dev/null || echo 0)"
    [ "$count" -gt 0 ] 2>/dev/null && behind="$count"
  fi
fi

if [ -n "$behind" ]; then
  branch="$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  if [ -n "$rebuilt" ]; then
    emit "\"web_ui/dist/app rebuilt. BUT this checkout ($branch) is $behind commit(s) behind origin/main, so the running app is still missing work that has already merged - the bundle can only ever be as current as the source it is built from. Merge or rebase when your working tree allows it.\""
  else
    emit "\"web_ui/dist/app matches this source, but the checkout ($branch) is $behind commit(s) behind origin/main - the running app is missing work that has already merged. Merge or rebase when your working tree allows it.\""
  fi
  exit 0
fi

[ -n "$rebuilt" ] && emit '"web_ui/dist/app rebuilt - the desktop app and :8765 now serve the current source."'
exit 0
