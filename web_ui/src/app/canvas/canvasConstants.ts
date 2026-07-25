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
