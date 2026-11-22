import type { GlyphDefinition, GlyphPrimitive } from "../registry";

// Monochrome silhouettes stay legible without status colors or animation.
const path = (d: string): GlyphPrimitive => ({ type: "path", d });
const line = (x1: number, y1: number, x2: number, y2: number): GlyphPrimitive =>
  ({ type: "line", x1, y1, x2, y2 });
const polyline = (points: string): GlyphPrimitive => ({ type: "polyline", points });
const circle = (cx: number, cy: number, r: number): GlyphPrimitive =>
  ({ type: "circle", cx, cy, r });
const dot = (cx: number, cy: number): GlyphPrimitive =>
  ({ type: "circle", cx, cy, r: 1.25, fill: true });

const shield = path(
  "M 10 2.5 L 16.5 5 V 10.5 C 16.5 14 13.5 16.5 10 18 " +
  "C 6.5 16.5 3.5 14 3.5 10.5 V 5 Z",
);

export const statusGlyphs = {
  "status-success": [circle(10, 10, 7.5), polyline("6 10.5 9 13.5 14.5 7.5")],
  "status-error": [
    path("M 7.5 3 H 12.5 L 17 7.5 V 12.5 L 12.5 17 H 7.5 L 3 12.5 V 7.5 Z"),
    line(7, 7, 13, 13), line(13, 7, 7, 13),
  ],
  "status-warning": [
    path("M 10 2.5 L 17.5 18 H 2.5 Z"),
    line(10, 7.5, 10, 10.5), dot(10, 14.5),
  ],
  "status-info": { alias: "info" },
  // Eight separate short perimeter segments: no stroke-dasharray dependency.
  "status-pending": [
    path("M 8.5 3 H 11.5 M 14 4.5 L 15.5 6 M 17 8.5 V 11.5 " +
      "M 15.5 14 L 14 15.5 M 11.5 17 H 8.5 M 6 15.5 L 4.5 14 " +
      "M 3 11.5 V 8.5 M 4.5 6 L 6 4.5"),
  ],
  "status-running": [circle(10, 10, 7.5), dot(10, 10)],
  "status-skipped": [
    circle(10, 10, 7.5),
    path("M 5.5 10 H 12 M 15 7 V 13"),
    polyline("9 7 12 10 9 13"),
  ],
  "status-cancelled": [circle(10, 10, 7.5), line(7, 13, 13, 7)],
  "status-stale": [
    path("M 16.5 10 C 16.5 13.5 13.5 16.5 10 16.5 " +
      "C 6.5 16.5 3.5 13.5 3.5 10 " +
      "C 3.5 6.5 6.5 3.5 10 3.5 " +
      "C 11.5 3.5 12.5 4 13.5 4.5"),
    polyline("11 6 13.5 4.5 12 2"), dot(10, 10),
  ],
  "status-approval": [shield, line(10, 7, 10, 10), dot(10, 14)],
  "status-approved": [shield, polyline("7 10.5 9.5 13 13.5 8.5")],
  "status-rejected": [
    circle(10, 10, 7.5), line(7, 7, 13, 13), line(13, 7, 7, 13),
  ],
} as const satisfies Record<string, GlyphDefinition>;
