/** Below this zoom, node bodies collapse to their title bar - the R1 seed of
 * the Qt canvas's LOD thresholds. Shared by every custom node view so they
 * all collapse at the same zoom level. */
export const LOD_ZOOM_THRESHOLD = 0.5;

/** R6.1: Notes/Frames/Containers. The backend owns all real size/position
 * math for frame/container nodes (backend/canvas.py's _recompute_group_bounds -
 * see that function's own doc for the padded-bbox-of-members algorithm) - the
 * frontend never computes a bbox itself, it only renders whatever
 * groupWidth/groupHeight the scene snapshot says. These two pairs exist
 * purely as: (a) a UI-only floor for the live NodeResizer drag gesture
 * (GroupNodeView.tsx) - the backend's own resize_frame does the AUTHORITATIVE
 * clamp against the true bbox-of-members minimum, this is just a sane local
 * floor so the handle doesn't visually collapse to nothing mid-drag; and (b)
 * a defensive fallback size for the rare wire moment a frame/container's
 * groupWidth/groupHeight legitimately reads null (before its very first
 * _recompute_group_bounds lands) - in practice create_frame/create_container
 * both call that immediately at construction, so this fallback should be
 * unreachable in normal operation. */
export const GROUP_RESIZE_MIN_WIDTH = 160;
export const GROUP_RESIZE_MIN_HEIGHT = 90;
export const GROUP_FALLBACK_WIDTH = 320;
export const GROUP_FALLBACK_HEIGHT = 200;

/** R6.2: Chart node. Legacy's own size bounds
 * (graphlink_canvas_chart_item.py's MIN_WIDTH/MIN_HEIGHT/MAX_WIDTH/
 * MAX_HEIGHT) - the backend (backend/canvas.py's resize_chart) is the
 * AUTHORITATIVE clamp, same posture as GROUP_RESIZE_MIN_WIDTH/HEIGHT above;
 * these are just the UI-side floor/ceiling so the <NodeResizer/> handle
 * itself never drags past what the server would clamp back anyway.
 * DEFAULT_WIDTH/HEIGHT mirror backend/canvas.py's own SceneNode field
 * defaults (chart_width/chart_height), exported here purely so the frontend
 * has a single source for that number if it ever needs one (e.g. a
 * defensive fallback) rather than re-hardcoding 680/500 elsewhere. */
export const CHART_MIN_WIDTH = 440;
export const CHART_MIN_HEIGHT = 320;
export const CHART_MAX_WIDTH = 2400;
export const CHART_MAX_HEIGHT = 1800;
export const CHART_DEFAULT_WIDTH = 680;
export const CHART_DEFAULT_HEIGHT = 500;

/** How long to wait, after a <NodeResizer/> drag settles (onResizeEnd), before
 * actually firing the resizeChart intent - a guard against a user chaining
 * several quick resize gestures in a row each triggering its own network
 * round trip + real matplotlib re-render server-side (see
 * graphlink_chart_rendering.py) - not a per-pixel throttle (onResizeEnd
 * itself already only fires once per completed drag gesture, same as
 * GroupNodeView's own onResize wiring). Legacy's own equivalent debounced
 * 90ms after a same-process Qt repaint; this is intentionally longer since a
 * resize-then-WS-round-trip in this stack has a different cost profile. */
export const CHART_RESIZE_DEBOUNCE_MS = 200;

/** R6.3: Scene-level serialization gaps. Three independent "report after
 * settling, not on every intermediate frame" debounces - same guard-against-
 * a-network-call-burst posture as CHART_RESIZE_DEBOUNCE_MS above, just
 * applied to three different gestures (canvas pan/zoom, the HTML node's
 * splitter drag, a chat node's own content scroll) instead of NodeResizer's
 * onResizeEnd. */
export const VIEWPORT_REPORT_DEBOUNCE_MS = 250;
export const HTML_SPLITTER_REPORT_DEBOUNCE_MS = 200;
export const CHAT_SCROLL_REPORT_DEBOUNCE_MS = 200;

/** How long node re-measurements settle before being reported to the
 * backend (reportNodeSizes), which needs them to fit a frame/container
 * around its members - the backend cannot compute a chat node's rendered
 * height for itself. Same debounce posture as the three above, but the
 * burst it guards is different in kind: a streaming reply re-measures its
 * node on nearly every token, so this is the difference between one report
 * per settled layout and hundreds per reply. */
export const NODE_SIZE_REPORT_DEBOUNCE_MS = 200;

/** R6.3: HTML view node splitter-position scaffolding. Legacy's own
 * splitter_state (graphlink_html_view.py's QSplitter) was a deliberate scope
 * cut back in R3.17/R3.18 (see HtmlNodeView.tsx/styles.css's own now-
 * superseded comments) - it was pure Qt UI state with no domain meaning AT
 * THE TIME, confirmed by a since-removed test asserting scene_payload needed
 * no new key for it. R6.3 re-scopes it back in because R6.4/R6.5's session
 * load/save pipeline needs every legacy-persisted field to round-trip
 * losslessly, this one included - a value this app itself never wrote before
 * can still show up in an OLD chats.db row R6.4 loads. HTML_SPLIT_TOTAL_PX is
 * the combined source+preview pane height the split fraction is measured
 * against, chosen to equal the pre-R6.3 fixed 140px+140px total exactly, so
 * the default 0.5 fraction renders pixel-identical to the old fixed layout.
 * MIN/MAX keep either pane from being dragged down to zero height. */
export const HTML_SPLIT_TOTAL_PX = 280;
export const HTML_SPLIT_DEFAULT = 0.5;
export const HTML_SPLIT_MIN = 0.15;
export const HTML_SPLIT_MAX = 0.85;
