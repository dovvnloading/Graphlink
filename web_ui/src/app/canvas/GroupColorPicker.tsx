import { useRef, useState, type CSSProperties } from "react";
import { IconButton } from "../ui/IconButton";
import { MenuGroup, MenuItem } from "../ui/Menu";
import { NodeMenu } from "./NodeMenu";
import { swatchStyle } from "./nodeSwatch";

/** Persisted colour choices are actual values, not theme-dependent CSS
 * tokens: saved notes and frames must keep their chosen hue in either theme. */
export interface NamedColor {
  name: string;
  hex: string;
}

export const GROUP_NAMED_COLORS: NamedColor[] = [
  { name: "Green", hex: "#3f8f5c" },
  { name: "Blue", hex: "#3f7dc9" },
  { name: "Purple", hex: "#8a5fd1" },
  { name: "Orange", hex: "#d98a3d" },
  { name: "Red", hex: "#cf5354" },
  { name: "Yellow", hex: "#d9b23d" },
];
export const GROUP_MONO_COLORS: NamedColor[] = [
  { name: "Mid Gray", hex: "#7a7a7a" },
  { name: "Dark Gray", hex: "#454545" },
];

export interface GroupColorPickerProps {
  color: string | null;
  headerColor: string | null;
  onSelect: (color: string | null, headerColor: string | null) => void;
}

export function GroupColorPicker({ color, headerColor, onSelect }: GroupColorPickerProps) {
  const triggerRef = useRef<HTMLDivElement>(null);
  // Capture the anchor only when opening; a nested kit ContextMenu provides
  // keyboard navigation, outside-click dismissal and focus restoration.
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null);
  const open = anchor !== null;
  function toggle() {
    if (open) { setAnchor(null); return; }
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) setAnchor({ x: rect.left, y: rect.bottom + 6 });
  }
  function choose(nextColor: string | null, nextHeaderColor: string | null) {
    onSelect(nextColor, nextHeaderColor);
    setAnchor(null);
  }
  function colorItem(c: NamedColor, headerOnly: boolean) {
    const chosen = headerOnly ? headerColor === c.hex : color === c.hex;
    return (
      <div key={c.name} className="group-color-option"
        style={{ "--group-option-color": c.hex } as CSSProperties}>
        <MenuItem label={headerOnly ? `${c.name} Header` : c.name}
          checked={chosen || undefined} onSelect={() => choose(headerOnly ? color : c.hex, headerOnly ? c.hex : headerColor)} />
      </div>
    );
  }
  return (
    <div className="group-color-picker nodrag" ref={triggerRef}>
      <IconButton size="sm" icon="palette" label="Set color"
        className="group-color-swatch-trigger"
        style={swatchStyle(headerColor ?? color)} aria-haspopup="menu"
        aria-expanded={open} onClick={toggle} />
      {anchor && (
        <NodeMenu position={anchor} onClose={() => setAnchor(null)}
          ignoreRef={triggerRef} className="group-color-popover" ariaLabel="Color">
          <MenuGroup label="Body">{GROUP_NAMED_COLORS.map((c) => colorItem(c, false))}</MenuGroup>
          <MenuGroup label="Header only">{GROUP_NAMED_COLORS.map((c) => colorItem(c, true))}</MenuGroup>
          <MenuGroup label="Monochrome">{GROUP_MONO_COLORS.map((c) => colorItem(c, false))}</MenuGroup>
          <MenuItem icon="refresh" label="Reset to Default" onSelect={() => choose(null, null)} />
        </NodeMenu>
      )}
    </div>
  );
}
