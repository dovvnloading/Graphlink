# Node Rendering and UI QA Review

**Date:** 2026-07-15  
**Scope:** QGraphics node rendering, canvas navigation, connection rendering, and primary window UI  
**Status:** Findings documented and proposed local patch implemented; not pushed

## Executive summary

The app has a coherent visual direction and the main node families render successfully, but the canvas still has several correctness and interaction problems that keep it below production/SOTA quality:

- geometry change notifications are ordered incorrectly in two node families and in connections;
- long Code nodes visibly truncate content with no way to scroll;
- canvas navigation has incomplete coverage for node types and can desynchronize zoom state;
- connection and scene rendering are tightly coupled to a live view;
- repaint and connection-update work is broader than necessary during interaction.

The highest-value fixes have now been implemented locally: geometry and connection updates are ordered safely, long-form canvas content is scrollable, zoom/Fit All behavior is centralized, and the main canvas render paths use shared typography settings.

## Verification performed

- `python -m pytest -q` from `graphlink_app/`: **400 passed**, one unrelated deprecation warning.
- `python -m compileall -q .`: passed.
- Targeted headless canvas regression suite: **7 passed**.
- Headless Qt scene smoke render with long Chat, Code, Document, and Thinking nodes: all constructed and painted without an exception.
- Headless main-window construction/render at 1200×800 and 800×600: passed. At the narrower size, Qt moves most toolbar actions into its overflow area.
- No model/API calls, file attachments, or GitHub operations were performed.

## Local implementation status

- Findings 1 and 2: fixed. Document, Thinking, and connection geometry now prepare before bounds/path mutation; pin movement updates paths after the position change.
- Findings 3 and 4: fixed. CodeNode has a bounded internal scrollbar and the view routes wheel events by scroll capability rather than a short concrete-type list.
- Findings 5 and 6: fixed. Fit All uses persistent visible scene items, and toolbar/keyboard/Ctrl-wheel zoom share `ChatView.zoom_by()` with bounds and LOD refresh.
- Finding 7: fixed for bounded preview rendering and invalid-image feedback. The existing image context menu remains the full-size/save path.
- Finding 8: fixed. Connection culling is skipped safely when no view is attached.
- Finding 9: addressed. The view uses minimal viewport updates, connection paths use no stale device cache, node moves use an endpoint index, and minimap scene-change notifications are coalesced per event-loop turn.
- Finding 10: addressed for canvas node headers and canvas items, including Code, Image, Note, Frame, Container, and plugin node headers through shared typography helpers. Embedded plugin widget styles remain intentionally scoped to their own controls.

The patch is intentionally still local. GitHub commit/push/PR actions were not performed.

The current automated suite is primarily headless/unit coverage; it does not validate real mouse-wheel routing, drag gestures, hover states, or screenshots on a Windows desktop.

## Findings

### 1. Geometry change notification happens after the geometry mutation

**Severity:** High  
**Locations:** `graphlink_nodes/graphlink_node_document.py:263-284`, `graphlink_nodes/graphlink_node_thinking.py:87-108`

`DocumentNode` and `ThinkingNode` assign a new `height` and only then call `prepareGeometryChange()`. Qt requires `prepareGeometryChange()` before any value used by `boundingRect()` changes. The code also calls it twice in the Thinking path.

**Impact:** After font changes or other in-scene recalculation, the scene's spatial index, hit testing, culling, and parent/container geometry can retain the old bounds. Symptoms can include clicks missing the visible node, stale selection regions, or content/connection repaint not matching the new size.

**Proposal:** Introduce one geometry-update helper per node family that computes the new dimensions first, calls `prepareGeometryChange()` exactly once when the dimensions actually change, applies the values, then updates scrollbar geometry and connections. Add a regression test that changes font size while the node is already in a `QGraphicsScene` and verifies `scene.items()`/`itemAt()` against the new bounds.

### 2. Connection paths mutate before `prepareGeometryChange()`

**Severity:** High  
**Location:** `graphlink_connections.py:484-489`

`ConnectionItem.update_path()` assigns `self.path = new_path`, clears the hover cache, and calls `prepareGeometryChange()` afterward.

**Impact:** The connection's `boundingRect()` and `shape()` can be indexed using stale geometry. This is especially risky while dragging nodes or pins, where the path changes repeatedly; hit targets, culling, and cached repaint regions can lag behind the visible line.

**Proposal:** Call `prepareGeometryChange()` before replacing `self.path`, then invalidate `hover_path`, assign the new path, and update. Add drag/pin tests that assert the connection bounding rectangle and hit shape follow the new endpoints on every update.

### 3. Code nodes silently truncate long content

**Severity:** High  
**Locations:** `graphlink_nodes/graphlink_node_code.py:73-78`, `graphlink_nodes/graphlink_node_code.py:151-156`

The node caps itself at `MAX_HEIGHT = 800`, clips the `QTextDocument` to that rectangle, and has no scrollbar or paging control. The headless render reproduced this with a 30-line repeated code sample: the node reached height 800 and had no scroll control, while Chat, Document, and Thinking nodes exposed scrollbars for overflow.

**Impact:** Generated or pasted code beyond the cap is not discoverable in the canvas. This is a correctness issue for a code workspace, not only a visual limitation.

**Proposal:** Give CodeNode the same scroll model as the other long-form nodes, including a visible scrollbar, clamped scroll range, and wheel routing. Preserve the fixed maximum node height. Add a test that verifies content below the viewport becomes visible after scrolling.

### 4. Wheel-event routing only recognizes Chat and Document nodes

**Severity:** Medium  
**Location:** `graphlink_view.py:614-623`

`_scrollable_item_at()` stops only when it finds `ChatNode` or `DocumentNode`. `ThinkingNode` and `Note` both implement internal scrolling but are not recognized, so the view treats the wheel as canvas navigation instead of forwarding it to the item.

**Impact:** Wheel behavior changes by node type. Users can scroll long Chat/Document content in place but accidentally pan away from long Thinking/Note content, making the internal scrollbar difficult or impossible to use naturally.

**Proposal:** Replace the concrete type tuple with a small scrollable-item protocol/capability check (`scrollbar`, collapsed state, and a handled wheel event), then walk proxy/child items to the owning item. Add simulated wheel tests for Chat, Document, Thinking, and Note.

### 5. “Fit All” ignores most visual item families

**Severity:** Medium  
**Location:** `graphlink_view.py:720-726`

The early-return guard checks only `scene.nodes`, `scene.code_nodes`, and `scene.image_nodes`. It ignores Document, Thinking, Chart, plugin, Note, Frame, and Container lists. A headless check with a scene containing only a Document or only a Thinking node left the transform at 1.0 and did not fit the item.

**Impact:** Fit All appears to do nothing for valid sessions composed of attachment/reasoning/plugin/canvas items without a Chat/Code/Image node.

**Proposal:** Base the guard on a single scene-level visual-item query, with explicit filtering for transient/hidden items. Use the same query for `itemsBoundingRect()` and minimap population so fit, navigation, and overview cannot disagree. Add one test per representative family and a mixed-scene test.

### 6. Toolbar zoom bypasses `_zoom_factor`

**Severity:** Medium  
**Locations:** `graphlink_window.py:640-641`, `graphlink_view.py:559-577`, `graphlink_view.py:632-640`

The toolbar buttons call `chat_view.scale()` directly, while keyboard and Ctrl+wheel paths update `_zoom_factor` and enforce 0.1–4.0 limits.

**Impact:** After using toolbar zoom, subsequent keyboard/Ctrl+wheel zoom starts from stale state. The effective transform can exceed the intended limits or stop earlier/later than expected, and the LOD refresh behavior becomes inconsistent.

**Proposal:** Add one `zoom_by(factor, anchor)` method that clamps against the actual transform, updates `_zoom_factor`, and schedules LOD refresh. Route toolbar, keyboard, wheel, reset, and fit actions through it. Add boundary and mixed-input tests.

### 7. Image node height is unbounded by source aspect ratio

**Severity:** Medium  
**Location:** `graphlink_nodes/graphlink_node_image.py:32-41`

Image height is calculated directly from the decoded image aspect ratio. There is no maximum display height, thumbnail mode, or scroll/pan viewport. An unusually tall attachment can therefore create a very large node and make layout/fit behavior unusable; invalid image data produces a blank dark body without an explicit placeholder.

**Proposal:** Render images through a bounded preview viewport with a maximum display dimension, preserve aspect ratio, and expose an “open/full size” action for large assets. Draw a visible invalid-image state when `QImage.fromData()` returns null. Add tests for panoramic, portrait, and corrupt bytes.

### 8. Connection paint assumes at least one attached view

**Severity:** Medium  
**Location:** `graphlink_connections.py:618-620`

`ConnectionItem.paint()` unconditionally reads `self.scene().views()[0]` for culling. Scene rendering, export, headless QA, and teardown can legitimately occur with no attached view.

**Impact:** Rendering a scene without a view can raise `IndexError`; the connection is more fragile than other scene items and cannot be safely rendered in isolation.

**Proposal:** Make culling optional: use the painter/widget viewport when available, otherwise skip view-based culling and paint the path. Apply the same defensive pattern to any view-dependent helper. Add a scene-to-image test with no `QGraphicsView` attached.

### 9. Canvas repaint and connection updates are too broad during interaction

**Severity:** Medium  
**Locations:** `graphlink_view.py:35`, `graphlink_scene.py:470-500`, `graphlink_scene.py:888-924`

The view uses `FullViewportUpdate`. Each node move walks every connection list, and `scene_changed` is emitted for every movement. The base connection also uses `DeviceCoordinateCache` while its path is mutated frequently.

**Impact:** Dragging and large graphs will do unnecessary full-viewport work and can produce frame drops, stale cache invalidation, or high CPU usage as node/connection counts grow.

**Proposal:** Batch drag updates with a short timer or drag lifecycle, update only connections indexed by endpoint, and emit one scene-change notification at drag end. Prefer Qt's minimal/bounding-rect viewport updates after validating correctness. Benchmark 50/250/500-node graphs with and without animated arrows before choosing the final cache mode.

### 10. Font/theme controls do not cover the full visual system

**Severity:** Low–Medium  
**Locations:** `graphlink_scene.py:189-200`, `graphlink_nodes/graphlink_node_code.py:136-146`, `graphlink_nodes/graphlink_node_image.py:94-99`

The canvas Font control is presented as a global control, but `_update_all_node_fonts()` updates only Chat, Document, and Thinking nodes. Code and Image headers still select their own hard-coded fonts, and many plugin/canvas items retain independent typography and colors.

**Impact:** Changing the setting produces a partially updated canvas with inconsistent typography and geometry. This is especially visible when comparing a Chat node with Code/Image/plugin nodes.

**Proposal:** Either scope the control explicitly to “rich text nodes” or introduce a theme/typography token object consumed by every render path. Avoid per-file font literals for shared roles; recalculate geometry only for items whose metrics changed. Add a screenshot/metric test for a mixed node set after font changes.

## Recommended implementation order

1. Fix `prepareGeometryChange()` ordering for nodes and connections.
2. Add CodeNode scrolling and unify scroll-wheel routing.
3. Centralize fit-all and zoom behavior.
4. Make connection paint safe without a view.
5. Batch connection/repaint work and benchmark large graphs.
6. Finish typography/theme consistency and responsive UI polish.

## Suggested acceptance checks

- Long Chat, Code, Document, Thinking, and Note content can be read end-to-end with the wheel and scrollbar.
- Moving/resizing nodes updates connection paths and hit targets immediately without trails or missed clicks.
- Fit All works with every persisted node family, including a scene containing only one family.
- Toolbar, keyboard, and Ctrl+wheel zoom share one bounded state.
- A scene can render to an image with zero attached views.
- Mixed-node font/theme changes update consistently and preserve readable contrast.
- A Windows desktop smoke pass covers 100%, 125%, and 150% display scaling plus narrow (800px) and wide (1920px) windows.
