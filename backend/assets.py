"""Binary asset serving for the new architecture (Qt-removal plan R3.21,
extended R6.2).

Image-node bytes never travel over the WS scene snapshot - see the
transport-decision comment on backend/canvas.py's SceneDocument.image_assets
for why (scene_payload() resends every node on every publish_scene() call,
so inlined bytes there would compound in size on every unrelated mutation).
Instead the frontend fetches them on demand from this dedicated HTTP route,
addressed by the opaque image_asset_id each image-kind SceneNode carries.
R6.2's chart nodes REUSE this exact same route/dict for their own
display-resolution PNG (chart_asset_id, same image_assets store) - no
parallel asset store.

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
"""

from __future__ import annotations

import re

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from backend.events import EventBus
from backend.session_context import get_session_context
from graphlink_chart_rendering import render_chart_png

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


def register_assets(app: FastAPI, bus: EventBus) -> None:
    """Give the app its asset routes: GET /api/assets/{asset_id} (cached
    display bytes, any image-kind or chart-kind node) and GET /api/assets/
    chart/{node_id}/export (a fresh 3x-resolution chart re-render, R6.2)."""

    @app.get("/api/assets/{asset_id}")
    async def get_asset(asset_id: str, session: str = "default") -> Response:
        document = get_session_context(bus.session(session)).canvas_document
        asset = document.get_image_asset(asset_id)
        if asset is None:
            return JSONResponse({"error": "unknown asset"}, status_code=404)
        image_bytes, mime_type = asset
        return Response(content=image_bytes, media_type=mime_type)

    @app.get("/api/assets/chart/{node_id}/export")
    async def export_chart(node_id: str, session: str = "default") -> Response:
        document = get_session_context(bus.session(session)).canvas_document
        node = document.nodes.get(node_id)
        if node is None or node.kind != "chart":
            return JSONResponse({"error": "unknown chart"}, status_code=404)

        png_bytes = render_chart_png(
            node.state.chart_type,
            node.state.chart_data,
            node.state.chart_width,
            node.state.chart_height,
            dpi_scale=CHART_EXPORT_DPI_SCALE,
        )
        title = node.state.chart_data.get("title") if isinstance(node.state.chart_data, dict) else ""
        filename = _sanitize_chart_filename(title)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{filename}.png"'},
        )
