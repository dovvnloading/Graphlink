"""Binary asset serving for the new architecture (Qt-removal plan R3.21,
extended R6.2).

Image-node bytes never travel over the WS scene snapshot - see the
transport-decision comment on backend/canvas.py's SceneDocument.image_assets
for why (scene_payload() resends every node on every publish_scene() call,
so inlined bytes there would compound in size on every unrelated mutation).
Instead the frontend fetches them on demand from this dedicated HTTP route,
addressed by the opaque image_asset_id each image-kind SceneNode carries.
R6.2's chart nodes used to REUSE this exact same route/dict for their own
display-resolution PNG (chart_asset_id, same image_assets store) - ADR-013
stage 13.4 retired that render outright (see ChartState's own docstring,
backend/domain/node_states.py), so this route today serves only real
image-kind nodes.

This route needs to reach the SAME SceneDocument instance register_canvas()
built for a given session - not a fresh one - so it goes through the same
EventBus.session(session_id) lookup /ws already uses (and defaults to
"default" the same way), then reads the document via
backend/session_context.py's get_session_context(), which is what makes
the document reachable here at all - see backend/app.py's
_configure_session for where it's attached.

R6.2 ALSO adds a second, genuinely new route: GET /api/assets/chart/{node_id}
/export. Unlike the cached display PNG above, chart export is a real 3x-
resolution RE-RENDER (legacy ChartItem.EXPORT_SCALE), not a lookup of
anything cached in image_assets - so it is a distinct endpoint, not a query
flag on the shared one.

ADR-013 stage 13.4: export_chart's render (~50-108ms of synchronous
matplotlib work, measured) is the LAST inline render this app makes -
add_chart_node/resize_chart's own display-PNG renders were retired outright
once stage 13.2's client-side interactive renderer made them dead weight
(see ChartState's own docstring, backend/domain/node_states.py). Wrapped in
asyncio.to_thread so this route no longer blocks the event loop (closing
C6/M1's own finding) - a plain `async def` route handler awaiting a
synchronous call would otherwise still stall every other in-flight request
on this same process for the full render duration. Also gained a real SVG
export option this same stage - vector, so no dpi_scale multiplier applies.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from backend.asset_store import ALLOWED_IMAGE_MIME_TYPES, extension_for_mime
from backend.domain.node_access import optional_node
from backend.domain.node_states import ChartState
from backend.events import EventBus, UnknownSessionError
from backend.session_context import get_session_context
from graphlink_chart_rendering import render_chart_png, render_chart_svg

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument

# Fallback Content-Type for a stored mime_type that is not one of this app's
# own real image types (ALLOWED_IMAGE_MIME_TYPES). octet-stream rather than
# an image/* guess: it tells every consumer (browser tab, <img> tag, future
# download affordance) "do not try to render this as anything", which is
# the whole point - the caller who wrote the bad mime_type also fully
# controls the bytes behind it.
_FALLBACK_MIME_TYPE = "application/octet-stream"

# 3x the display resolution - mirrors legacy ChartItem.EXPORT_SCALE exactly.
CHART_EXPORT_DPI_SCALE = 3.0


def _sanitize_chart_filename(title: str) -> str:
    """Port of legacy ChartItem._desktop_export_path's own sanitization
    intent (alnum/space/dash/underscore only, whitespace collapsed to a
    single underscore, "chart" fallback for an empty result) - not
    byte-identical, since this feeds an HTTP Content-Disposition header
    rather than a local filesystem path: characters are additionally
    restricted to ASCII so the header value can never carry raw non-ASCII
    bytes (isalnum() alone accepts non-ASCII letters, which a plain
    unescaped header value cannot safely carry)."""
    text = str(title or "")
    safe = "".join(
        ch for ch in text if (ch.isalnum() and ch.isascii()) or ch in (" ", "-", "_")
    ).strip()
    safe = re.sub(r"\s+", "_", safe)
    return safe or "chart"


def _get_canvas_document(bus: EventBus, session: str) -> "SceneDocument | None":
    """Resolves `session` to the SAME SceneDocument instance register_canvas()
    built for it (bus.session(session) -> get_session_context(...).
    canvas_document, per this module's own docstring above), or None for an
    unknown session id. ADR-004 stage 4.3: `None` here is the same
    observable "unknown resource" 404 a bogus session already produced
    before this stage (bus.session() used to silently CREATE a fresh, empty
    document for any string - see backend/events.py's own docstring - which
    would have looked up the requested resource in that empty document and
    hit the exact same 404 anyway). Each caller supplies its own
    resource-specific error message for the None case."""
    try:
        return get_session_context(bus.session(session)).canvas_document
    except UnknownSessionError:
        return None


def register_assets(app: FastAPI, bus: EventBus) -> None:
    """Give the app its asset routes: GET /api/assets/{asset_id} (cached
    display bytes, any image-kind or chart-kind node) and GET /api/assets/
    chart/{node_id}/export (a fresh 3x-resolution chart re-render, R6.2)."""

    @app.get("/api/assets/{asset_id}")
    async def get_asset(asset_id: str, session: str = "default") -> Response:
        document = _get_canvas_document(bus, session)
        if document is None:
            return JSONResponse({"error": "unknown asset"}, status_code=404)
        asset = document.get_image_asset(asset_id)
        if asset is None:
            return JSONResponse({"error": "unknown asset"}, status_code=404)
        image_bytes, mime_type = asset
        # Neither write path into document.image_assets (addImageNode's
        # caller-supplied mime_type, session_load._restore_image_payload's
        # payload-supplied mime_type) validates the string before storing
        # it, so it is not trustworthy here - pass it through only if it is
        # one of this app's own real image types. ALLOWED_IMAGE_MIME_TYPES
        # excludes image/svg+xml (see asset_store.py's SECURITY-FIX comment
        # on that set) so a stored SVG lands in the octet-stream fallback
        # below same as any other untrusted mime_type.
        if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
            mime_type = _FALLBACK_MIME_TYPE
        # SECURITY-FIX: this route used to set neither header at all. nosniff
        # stops a browser from content-sniffing these bytes into something
        # more dangerous than the declared Content-Type regardless of what
        # mime_type ends up being; Content-Disposition: inline (still
        # renders in an <img src>/fetch consumer, unlike "attachment") names
        # a real extension explicitly instead of leaving a future top-level-
        # document/iframe consumer to guess from the bytes themselves.
        extension = extension_for_mime(mime_type)
        return Response(
            content=image_bytes,
            media_type=mime_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f'inline; filename="{asset_id}.{extension}"',
            },
        )

    @app.get("/api/assets/chart/{node_id}/export")
    async def export_chart(node_id: str, session: str = "default", fmt: str = "png") -> Response:
        document = _get_canvas_document(bus, session)
        if document is None:
            return JSONResponse({"error": "unknown chart"}, status_code=404)
        node = optional_node(document.nodes, node_id, "chart", ChartState)
        if node is None:
            return JSONResponse({"error": "unknown chart"}, status_code=404)

        normalized_format = str(fmt or "png").strip().lower()
        if normalized_format not in ("png", "svg"):
            return JSONResponse({"error": "unsupported export format"}, status_code=400)

        title = node.state.chart_data.get("title") if isinstance(node.state.chart_data, dict) else ""
        filename = _sanitize_chart_filename(str(title or ""))

        if normalized_format == "svg":
            svg_bytes = await asyncio.to_thread(
                render_chart_svg,
                node.state.chart_type,
                node.state.chart_data,
                node.state.chart_width,
                node.state.chart_height,
            )
            return Response(
                content=svg_bytes,
                media_type="image/svg+xml",
                headers={"Content-Disposition": f'attachment; filename="{filename}.svg"'},
            )

        png_bytes = await asyncio.to_thread(
            render_chart_png,
            node.state.chart_type,
            node.state.chart_data,
            node.state.chart_width,
            node.state.chart_height,
            dpi_scale=CHART_EXPORT_DPI_SCALE,
        )
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{filename}.png"'},
        )
