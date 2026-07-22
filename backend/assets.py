"""Binary asset serving for the new architecture (Qt-removal plan R3.21).

Image-node bytes never travel over the WS scene snapshot - see the
transport-decision comment on backend/canvas.py's SceneDocument.image_assets
for why (scene_payload() resends every node on every publish_scene() call,
so inlined bytes there would compound in size on every unrelated mutation).
Instead the frontend fetches them on demand from this dedicated HTTP route,
addressed by the opaque image_asset_id each image-kind SceneNode carries.

This route needs to reach the SAME SceneDocument instance register_canvas()
built for a given session - not a fresh one - so it goes through the same
EventBus.session(session_id) lookup /ws already uses (and defaults to
"default" the same way), then reads the document off the SessionBus. See
backend/app.py's _configure_session for the (small) structural change that
makes the document reachable there.
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from backend.events import EventBus


def register_assets(app: FastAPI, bus: EventBus) -> None:
    """Give the app its one asset route: GET /api/assets/{asset_id}."""

    @app.get("/api/assets/{asset_id}")
    async def get_asset(asset_id: str, session: str = "default") -> Response:
        document = bus.session(session).canvas_document
        asset = document.get_image_asset(asset_id)
        if asset is None:
            return JSONResponse({"error": "unknown asset"}, status_code=404)
        image_bytes, mime_type = asset
        return Response(content=image_bytes, media_type=mime_type)
