import type { GlyphDefinition, GlyphPrimitive } from "../registry";

// Geometry stays on the 20-unit, half-unit-snapped grid; Icon owns the stroke.
const path = (d: string): GlyphPrimitive => ({ type: "path", d });
const line = (x1: number, y1: number, x2: number, y2: number): GlyphPrimitive =>
  ({ type: "line", x1, y1, x2, y2 });
const polyline = (points: string): GlyphPrimitive => ({ type: "polyline", points });
const circle = (cx: number, cy: number, r: number): GlyphPrimitive =>
  ({ type: "circle", cx, cy, r });
const dot = (cx: number, cy: number): GlyphPrimitive =>
  ({ type: "circle", cx, cy, r: 1.25, fill: true });
const rect = (x: number, y: number, width: number, height: number): GlyphPrimitive =>
  ({ type: "rect", x, y, width, height, rx: Math.min(width, height) < 8 ? 1.5 : 2 });

export const nodeGlyphs = {
  "node-user": [
    circle(10, 6.5, 3),
    path("M 4 17 V 15 C 4 12 6.5 11 10 11 C 13.5 11 16 12 16 15 V 17"),
  ],
  "node-assistant": [
    circle(10, 10, 8),
    path("M 10 5 L 12 8 L 15 10 L 12 12 L 10 15 L 8 12 L 5 10 L 8 8 Z"),
  ],
  "node-thinking": [
    path("M 5.5 12.5 C 3.5 12.5 2.5 11 2.5 9 C 2.5 7 4 5.5 6 5.5 C 7 3 10.5 2.5 12.5 4.5 C 15.5 4 17.5 6 17.5 9 C 17.5 11 16 12.5 14 12.5 Z"),
    dot(7, 15), dot(3.5, 17),
  ],
  "node-code": [
    polyline("6 6 2.5 10 6 14"), line(11.5, 6.5, 8.5, 13.5),
    polyline("14 6 17.5 10 14 14"),
  ],
  "node-runner": [
    rect(3, 3, 14, 14), polyline("8 6.5 14 10 8 13.5 8 6.5"),
  ],
  "node-document": { alias: "document-text" },
  "node-html": [
    rect(2.5, 2.5, 15, 15), line(2.5, 9.5, 17.5, 9.5),
    dot(6, 6), dot(9.5, 6),
  ],
  "node-image": [
    rect(2.5, 3, 15, 14),
    polyline("4.5 14.5 8 10.5 11 13.5 13.5 11 15.5 14"),
    dot(13.5, 6.5),
  ],
  "node-media": [
    rect(2, 4, 16, 12), polyline("8 6.5 14 10 8 13.5 8 6.5"),
  ],
  "node-conversation": [
    path("M 7 3 H 15 C 16.5 3 17 4 17 5.5 V 11 C 17 12 16.5 12.5 15 12.5 H 14 L 14 15"),
    path("M 5 8 H 11 C 12 8 12.5 9 12.5 10 V 14 C 12.5 15 12 15.5 11 15.5 H 7 L 4.5 18 V 15.5 C 3 15.5 2.5 14.5 2.5 13.5 V 10.5 C 2.5 9 3.5 8 5 8 Z"),
  ],
  "node-web": [
    circle(10, 10, 7.5),
    path("M 10 2.5 C 6.5 6 6.5 14 10 17.5 M 10 2.5 C 13.5 6 13.5 14 10 17.5"),
    line(2.5, 10, 17.5, 10),
  ],
  "node-artifact": [
    rect(3, 5, 14, 12),
    path("M 3 8 L 10 11 L 17 8 M 10 11 V 17"),
  ],
  "node-git": [line(2.5, 10, 17.5, 10), circle(10, 10, 3.5)],
  "node-review": [
    path("M 4 2.5 H 12 L 16 6.5 V 17.5 H 4 Z M 12 2.5 V 6.5 H 16"),
    path("M 6.5 9.5 H 9.5 M 8 8 V 11 M 11.5 9.5 H 13.5 M 6.5 14 H 9.5 M 11.5 14 H 13.5"),
  ],
  "node-note": [
    path("M 3 3 H 17 V 13.5 L 13.5 17 H 3 Z M 13.5 17 V 13.5 H 17"),
  ],
  "node-model": [
    path("M 10 2.5 L 17 6.5 V 13.5 L 10 17.5 L 3 13.5 V 6.5 Z"),
    polyline("3 6.5 10 10 17 6.5"), line(10, 10, 10, 17.5),
  ],
  "node-science": [
    path("M 7 2.5 H 13 M 8 2.5 V 8 L 3 16 C 2.5 17 3.5 17.5 5 17.5 H 15 C 16.5 17.5 17.5 17 17 16 L 12 8 V 2.5"),
    line(5.5, 13, 14.5, 13),
  ],
  "node-chart": [
    path("M 3 3 V 17 H 17"), polyline("5.5 14 9 10 12 12 16 6"),
  ],
  "node-table": [
    rect(2.5, 2.5, 15, 15),
    path("M 7.5 2.5 V 17.5 M 12.5 2.5 V 17.5 M 2.5 7.5 H 17.5 M 2.5 12.5 H 17.5"),
  ],
  "node-plan": [
    path("M 4 6 L 6 8 L 9 4 M 4 13 L 6 15 L 9 11"),
    line(11.5, 6, 17, 6), line(11.5, 13, 17, 13),
  ],
  "node-agent": { alias: "agent" },
  "node-frame": [
    rect(3, 3, 14, 14),
    path("M 3 8 H 10 V 3"),
  ],
  "node-container": [rect(2.5, 3, 15, 14), line(2.5, 8, 17.5, 8)],
  "node-placeholder": [
    path("M 2.5 6 Q 2.5 2.5 6 2.5 M 8.5 2.5 H 11.5 M 14 2.5 Q 17.5 2.5 17.5 6 " +
      "M 17.5 8.5 V 11.5 M 17.5 14 Q 17.5 17.5 14 17.5 M 11.5 17.5 H 8.5 " +
      "M 6 17.5 Q 2.5 17.5 2.5 14 M 2.5 11.5 V 8.5"),
  ],
} as const satisfies Record<string, GlyphDefinition>;
