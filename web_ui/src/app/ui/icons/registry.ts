import { chromeGlyphs } from "./glyphs/chrome";
import { nodeGlyphs } from "./glyphs/nodes";
import { statusGlyphs } from "./glyphs/status";
import { featureGlyphs } from "./glyphs/feature";

// Glyphs are geometry only. The Icon component owns color, stroke and sizing.
export type GlyphPrimitive =
  | { readonly type: "path"; readonly d: string }
  | { readonly type: "circle"; readonly cx: number; readonly cy: number; readonly r: number; readonly fill?: true }
  | { readonly type: "rect"; readonly x: number; readonly y: number; readonly width: number; readonly height: number; readonly rx: number; readonly fill?: true }
  | { readonly type: "line"; readonly x1: number; readonly y1: number; readonly x2: number; readonly y2: number }
  | { readonly type: "polyline"; readonly points: string };

export type GlyphDefinition = readonly GlyphPrimitive[] | { readonly alias: string };

// Keep these four sources separate: later batches only add their own glyphs.
export const iconDefinitions = {
  ...chromeGlyphs,
  ...nodeGlyphs,
  ...statusGlyphs,
  ...featureGlyphs,
} as const satisfies Record<string, GlyphDefinition>;

export type IconName = keyof typeof iconDefinitions;
export const iconNames = Object.keys(iconDefinitions) as IconName[];

function resolveGlyph(name: IconName, ancestors: Set<string> = new Set()): readonly GlyphPrimitive[] {
  if (ancestors.has(name)) throw new Error("Circular icon alias: " + name);
  const entry: GlyphDefinition = iconDefinitions[name];
  if (!("alias" in entry)) return entry;

  if (!(entry.alias in iconDefinitions)) {
    throw new Error("Unknown icon alias target: " + name + " -> " + entry.alias);
  }
  ancestors.add(name);
  return resolveGlyph(entry.alias as IconName, ancestors);
}

// Expose concrete primitive arrays to consumers; aliases share their target's
// array instead of producing subtly different copies of the same concept.
export const iconRegistry: Readonly<Record<IconName, readonly GlyphPrimitive[]>> =
  Object.fromEntries(iconNames.map((name) => [name, resolveGlyph(name)])) as Record<IconName, readonly GlyphPrimitive[]>;
