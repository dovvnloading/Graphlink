import { useEffect, useRef, useState } from "react";

/**
 * The shared note/frame/container color-swatch popover (Qt-removal plan
 * R6.1) - the SPA equivalent of legacy's per-theme DARK_FRAME_COLORS/
 * MONO_FRAME_COLORS/MUTED_FRAME_COLORS palette
 * (graphlink_app/graphlink_styles.py), reused for Notes as well as
 * Frames/Containers per the R6.1 contract (SceneDocument.set_group_color
 * covers all three kinds identically).
 *
 * Named-color values here are DELIBERATELY NOT the same hex values legacy's
 * own DARK_FRAME_COLORS table carries (those are all near-identical grays -
 * `--gl-frame-green`/`--gl-frame-blue`/etc in gl-vars-dev.css all resolve to
 * #82-#8e, indistinguishable from one another) - this palette instead picks
 * real, visually-distinct hues for each name, matching this increment's own
 * scope decision (reasonable/sensible values, not pixel-parity with any
 * legacy theme). The NAME SET itself (Green/Blue/Purple/Orange/Red/Yellow
 * full-or-header, Mid Gray/Dark Gray full-only) is carried over exactly -
 * confirmed against DARK_FRAME_COLORS's own key set, including the fact that
 * only the 6 hued names ever get a "X Header" variant, never the 2 grays.
 *
 * Outside-click/Escape dismiss mirrors ThinkingNodeView.tsx's own
 * ThinkingNodeMenu - a local ref + pointerdown/keydown pair - rather than the
 * app-chrome OverlayProvider (overlays.tsx): that system's Popover is a
 * single named-surface registry (one "settings"/"view"/"plugins"/... slot at
 * a time), which does not fit a control that must be independently
 * instantiable per note/frame/container node on the canvas.
 */

export interface NamedColor {
  name: string;
  hex: string;
}

// The 6 hued colors - each selectable as either the note/group's full body
// color OR its header-only color (two sub-sections below).
export const GROUP_NAMED_COLORS: NamedColor[] = [
  { name: "Green", hex: "#3f8f5c" },
  { name: "Blue", hex: "#3f7dc9" },
  { name: "Purple", hex: "#8a5fd1" },
  { name: "Orange", hex: "#d98a3d" },
  { name: "Red", hex: "#cf5354" },
  { name: "Yellow", hex: "#d9b23d" },
];

// The 2 monochrome colors - full-color only, no header-only variant (mirrors
// DARK_FRAME_COLORS never defining "Mid Gray Header"/"Dark Gray Header").
export const GROUP_MONO_COLORS: NamedColor[] = [
  { name: "Mid Gray", hex: "#7a7a7a" },
  { name: "Dark Gray", hex: "#454545" },
];

// Exported so NoteNodeView's isSystemPrompt dashed border can stay visually
// consistent with the popover's own "Purple" swatch, rather than picking an
// unrelated one-off purple.
export const NOTE_SYSTEM_PROMPT_BORDER_COLOR = GROUP_NAMED_COLORS[2].hex;

export interface GroupColorPickerProps {
  color: string | null;
  headerColor: string | null;
  onSelect: (color: string | null, headerColor: string | null) => void;
}

export function GroupColorPicker({ color, headerColor, onSelect }: GroupColorPickerProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!ref.current?.contains(event.target as globalThis.Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  function choose(nextColor: string | null, nextHeaderColor: string | null) {
    onSelect(nextColor, nextHeaderColor);
    setOpen(false);
  }

  return (
    <div className="group-color-picker nodrag" ref={ref}>
      <button
        type="button"
        className="group-color-swatch-trigger"
        aria-label="Set color"
        aria-haspopup="true"
        aria-expanded={open}
        style={{ backgroundColor: headerColor ?? color ?? undefined }}
        onClick={() => setOpen((v) => !v)}
      />
      {open && (
        <div className="group-color-popover" role="menu" aria-label="Color">
          <p className="group-color-section-label">Body</p>
          <div className="group-color-row">
            {GROUP_NAMED_COLORS.map((c) => (
              <button
                key={c.name}
                type="button"
                role="menuitem"
                className={"group-color-swatch" + (color === c.hex ? " active" : "")}
                style={{ backgroundColor: c.hex }}
                aria-label={c.name}
                title={c.name}
                onClick={() => choose(c.hex, headerColor)}
              />
            ))}
          </div>
          <p className="group-color-section-label">Header Only</p>
          <div className="group-color-row">
            {GROUP_NAMED_COLORS.map((c) => (
              <button
                key={c.name}
                type="button"
                role="menuitem"
                className={"group-color-swatch" + (headerColor === c.hex ? " active" : "")}
                style={{ backgroundColor: c.hex }}
                aria-label={`${c.name} Header`}
                title={`${c.name} Header`}
                onClick={() => choose(color, c.hex)}
              />
            ))}
          </div>
          <p className="group-color-section-label">Monochrome</p>
          <div className="group-color-row">
            {GROUP_MONO_COLORS.map((c) => (
              <button
                key={c.name}
                type="button"
                role="menuitem"
                className={"group-color-swatch" + (color === c.hex ? " active" : "")}
                style={{ backgroundColor: c.hex }}
                aria-label={c.name}
                title={c.name}
                onClick={() => choose(c.hex, headerColor)}
              />
            ))}
          </div>
          <button type="button" role="menuitem" className="group-color-reset" onClick={() => choose(null, null)}>
            Reset to Default
          </button>
        </div>
      )}
    </div>
  );
}
