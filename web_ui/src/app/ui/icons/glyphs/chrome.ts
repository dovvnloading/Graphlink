import type { GlyphDefinition, GlyphPrimitive } from "../registry";

// All points sit on the 20-unit, half-unit-snapped grid. Helpers construct
// only the five permitted primitives; stroke and color stay in Icon.tsx.
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

const starOutline = path(
  "M 10 2.5 L 12.5 7 L 17.5 7.5 L 14 11 L 15 16.5 L 10 14 L 5 16.5 L 6 11 L 2.5 7.5 L 7.5 7 Z",
);
const pinHead = path("M 6 4 L 14 4 L 13 7.5 L 15.5 11 L 4.5 11 L 7 7.5 Z");

// Shared clockwise r~6.5 arc with a ~60-degree gap at the top-right.
// The two-stroke arrowhead sits in that gap, clear of the circle interior strokes.
const clockwiseArrowArc = path(
  "M 16.5 10 C 16.5 13.5 13.5 16.5 10 16.5 " +
  "C 6.5 16.5 3.5 13.5 3.5 10 " +
  "C 3.5 6.5 6.5 3.5 10 3.5 " +
  "C 11.5 3.5 12.5 4 13.5 4.5",
);
const clockwiseArrowHead = polyline("11 6 13.5 4.5 12 2");

export const chromeGlyphs = {
  close: [line(6, 6, 14, 14), line(14, 6, 6, 14)],
  check: [polyline("5 10.5 8.5 14 15 6.5")],
  plus: [line(10, 5, 10, 15), line(5, 10, 15, 10)],
  minus: [line(5, 10, 15, 10)],
  "chevron-down": [polyline("7 8 10 11 13 8")],
  "chevron-up": [polyline("7 12 10 9 13 12")],
  "chevron-left": [polyline("12 7 9 10 12 13")],
  "chevron-right": [polyline("8 7 11 10 8 13")],
  "arrow-up": [line(10, 16, 10, 4), polyline("6 8 10 4 14 8")],
  "arrow-down": [line(10, 4, 10, 16), polyline("6 12 10 16 14 12")],
  "arrow-left": [line(16, 10, 4, 10), polyline("8 6 4 10 8 14")],
  "arrow-right": [line(4, 10, 16, 10), polyline("12 6 16 10 12 14")],
  "external-link": [
    path("M 11 4 L 5.5 4 C 4.5 4 4 4.5 4 5.5 L 4 14.5 C 4 15.5 4.5 16 5.5 16 L 14.5 16 C 15.5 16 16 15.5 16 14.5 L 16 9.5"),
    path("M 10 10 L 17 3 M 12 3 L 17 3 L 17 8"),
  ],
  "more-horizontal": [dot(5, 10), dot(10, 10), dot(15, 10)],
  "more-vertical": [dot(10, 5), dot(10, 10), dot(10, 15)],
  search: [circle(8.5, 8.5, 5.5), line(12.5, 12.5, 16.5, 16.5)],
  undo: [polyline("8 6 4 10 8 14"), path("M 4 10 L 11 10 C 15 10 17 12 17 16")],
  redo: [polyline("12 6 16 10 12 14"), path("M 16 10 L 9 10 C 5 10 3 12 3 16")],
  "zoom-in": [
    circle(8.5, 8.5, 5.5), line(12.5, 12.5, 16.5, 16.5),
    line(6, 8.5, 11, 8.5), line(8.5, 6, 8.5, 11),
  ],
  "zoom-out": [
    circle(8.5, 8.5, 5.5), line(12.5, 12.5, 16.5, 16.5),
    line(6, 8.5, 11, 8.5),
  ],
  "fit-view": [
    path("M 7 3 H 3 V 7 M 13 3 H 17 V 7"),
    path("M 3 13 V 17 H 7 M 17 13 V 17 H 13"),
  ],
  organize: [
    rect(7, 2.5, 6, 4), rect(2.5, 12, 6, 4), rect(11.5, 12, 6, 4),
    path("M 10 6.5 V 9.5 H 5.5 V 12 M 10 9.5 H 14.5 V 12"),
  ],
  pin: [pinHead, line(10, 11, 10, 17)],
  "pin-filled": [pinHead, line(10, 11, 10, 17)],
  download: [
    line(10, 3, 10, 12), polyline("6 8 10 12 14 8"),
    path("M 4 13.5 V 16.5 H 16 V 13.5"),
  ],
  upload: [
    line(10, 12, 10, 3), polyline("6 7 10 3 14 7"),
    path("M 4 12.5 V 16.5 H 16 V 12.5"),
  ],
  "document-text": [
    path("M 5 2.5 H 12 L 16 6.5 V 16 C 16 17 15.5 17.5 14.5 17.5 H 5.5 C 4.5 17.5 4 17 4 16 V 4 C 4 3 4.5 2.5 5 2.5 Z M 12 2.5 V 6.5 H 16"),
    line(7, 10, 13, 10), line(7, 13.5, 13, 13.5),
  ],
  knowledge: [
    path("M 10 5.5 C 7.5 4 5 3.5 2.5 4.5 V 15.5 C 5 14.5 7.5 14.5 10 16 Z"),
    path("M 10 5.5 C 12.5 4 15 3.5 17.5 4.5 V 15.5 C 15 14.5 12.5 14.5 10 16 Z"),
  ],
  builder: [
    line(4.5, 15.5, 11.5, 8.5),
    path("M 15 2.5 V 6.5 M 13 4.5 H 17"),
    path("M 4 4.5 V 7.5 M 2.5 6 H 5.5"),
  ],
  agent: [rect(2.5, 3.5, 15, 13), polyline("5.5 7.5 8.5 10 5.5 12.5"), line(10, 12.5, 14.5, 12.5)],
  diagnostics: [polyline("2.5 10 6 10 8 5 11.5 15 14 10 17.5 10")],
  settings: [
    line(3, 5, 7, 5), circle(10, 5, 2), line(13, 5, 17, 5),
    line(2.5, 10, 4.5, 10), circle(7, 10, 2), line(10, 10, 17, 10),
    line(2.5, 15, 9.5, 15), circle(12.5, 15, 2), line(15.5, 15, 18, 15),
  ],
  help: [
    circle(10, 10, 8),
    path("M 7.5 7.5 C 7.5 5.5 9 5 10.5 5 C 13.5 5 14 8 11.5 10 L 10 11.5"),
    dot(10, 14.5),
  ],
  info: [circle(10, 10, 8), dot(10, 6), line(10, 9.5, 10, 14)],
  stop: [rect(6, 6, 8, 8)],
  attach: [
    path("M 12.5 6.5 L 7.5 12 C 5 14.5 8 17 10 14.5 L 15 9 C 18 5.5 14 2.5 11 5.5 L 5 12 C 2.5 14.5 6 18 9 16"),
  ],
  layers: [
    polyline("3 6 10 2.5 17 6 10 9.5 3 6"),
    polyline("3 10 10 13.5 17 10"),
    polyline("3 14 10 17.5 17 14"),
  ],
  compare: [rect(2.5, 3, 6, 11), rect(11.5, 5, 6, 11)],
  gauge: [
    path("M 3 15 C 2.5 8 6 4 10 4 C 14 4 17.5 8 17 15"),
    line(10, 13, 14, 8), dot(10, 13),
  ],
  copy: [
    path("M 5.5 14 H 5 C 4 14 3 13 3 12 V 5 C 3 4 4 3 5 3 H 12 C 13 3 14 4 14 5 V 6"),
    rect(7, 7, 10, 10),
  ],
  edit: [
    path("M 4 13 L 13.5 3.5 L 16.5 6.5 L 7 16 L 3.5 16.5 Z"),
    line(11.5, 5.5, 14.5, 8.5),
  ],
  trash: [
    path("M 5 7.5 H 15 L 14.5 16 C 14.5 17 14 17.5 13 17.5 H 7 C 6 17.5 5.5 17 5.5 16 Z"),
    line(3.5, 5.5, 16.5, 5.5), line(8, 3, 12, 3),
    line(8.5, 10, 8.5, 15), line(11.5, 10, 11.5, 15),
  ],
  refresh: [clockwiseArrowArc, clockwiseArrowHead],
  rerun: [
    clockwiseArrowArc,
    clockwiseArrowHead,
    polyline("8.5 7.5 13 10 8.5 12.5 8.5 7.5"),
  ],
  play: [polyline("7 5 15 10 7 15 7 5")],
  pause: [line(7, 5, 7, 15), line(13, 5, 13, 15)],
  keyboard: [
    rect(2, 5, 16, 11),
    dot(5, 9), dot(10, 9), dot(15, 9),
    line(6, 13, 14, 13),
  ],
  "panel-left": [rect(3, 3, 14, 14), line(8.5, 3, 8.5, 17)],
  maximize: [
    path("M 8 8 L 3 3 M 3 7 V 3 H 7 M 12 8 L 17 3 M 13 3 H 17 V 7"),
    path("M 8 12 L 3 17 M 3 13 V 17 H 7 M 12 12 L 17 17 M 13 17 H 17 V 13"),
  ],
  minimize: [
    path("M 3 3 L 8 8 M 4 8 H 8 V 4 M 17 3 L 12 8 M 12 4 V 8 H 16"),
    path("M 3 17 L 8 12 M 4 12 H 8 V 16 M 17 17 L 12 12 M 12 16 V 12 H 16"),
  ],
  grip: [dot(7, 5), dot(13, 5), dot(7, 10), dot(13, 10), dot(7, 15), dot(13, 15)],
  eye: [
    path("M 2.5 10 C 6 4.5 14 4.5 17.5 10 C 14 15.5 6 15.5 2.5 10 Z"),
    circle(10, 10, 2.5),
  ],
  "eye-off": [
    path("M 2.5 10 C 6 4.5 14 4.5 17.5 10 C 14 15.5 6 15.5 2.5 10 Z"),
    circle(10, 10, 2.5),
    line(3, 17, 17, 3),
  ],
  lock: [
    path("M 6.5 9 V 6 C 6.5 2.5 13.5 2.5 13.5 6 V 9"),
    rect(5, 9, 10, 8),
  ],
  unlock: [
    path("M 6.5 9 V 6 C 6.5 2.5 13.5 2.5 13.5 6 V 7"),
    rect(5, 9, 10, 8),
  ],
  link: [
    path("M 8.5 6.5 L 10 5 C 13.5 1.5 18 6 14.5 9.5 L 13 11"),
    path("M 7 9 L 5.5 10.5 C 2 14 6.5 18.5 10 15 L 11.5 13.5"),
    line(8, 12, 12, 8),
  ],
  unlink: [
    path("M 8 6.5 L 10 4.5 C 13 1.5 17.5 6 14.5 9 L 13.5 10"),
    path("M 6.5 10 L 5.5 11 C 2.5 14 7 18.5 10 15.5 L 12 13.5"),
    line(4, 16, 16, 4),
  ],
  filter: [path("M 3 4 H 17 L 12 10 V 16 L 8 18 V 10 Z")],
  clock: [circle(10, 10, 7.5), polyline("10 6 10 10 13.5 12")],
  history: [
    path("M 4.5 7 C 6.5 3 12.5 2 16 6 C 18.5 9.5 17 15 13 16.5 C 8 18.5 4 15 4 11"),
    polyline("3 3.5 4.5 7 8 6.5"),
    polyline("10 7 10 10 13 11.5"),
  ],
  star: [starOutline],
  "star-filled": [starOutline],
  archive: [
    rect(3.5, 7, 13, 10),
    path("M 2.5 4 H 17.5 V 7 H 2.5 Z"),
    line(8, 11, 12, 11),
  ],
  tag: [
    path("M 3 3.5 H 10 L 17 10.5 L 10.5 17 L 3 9.5 Z"),
    circle(6.5, 6.5, 1.5),
  ],
  folder: [
    path("M 2.5 6.5 V 5.5 C 2.5 4.5 3.5 4 4.5 4 H 8 L 10 6 H 15.5 C 17 6 17.5 7 17.5 8.5 V 15.5 C 17.5 17 16.5 17.5 15 17.5 H 5 C 3.5 17.5 2.5 17 2.5 15.5 Z"),
  ],
  "folder-open": [
    path("M 2.5 12 V 5.5 C 2.5 4.5 3.5 4 4.5 4 H 8 L 10 6 H 15 C 16 6 17 7 17 8"),
    path("M 4.5 9 H 17.5 L 15 17 H 2.5 Z"),
  ],
  file: [
    path("M 5 2.5 H 12 L 16 6.5 V 16 C 16 17 15.5 17.5 14.5 17.5 H 5.5 C 4.5 17.5 4 17 4 16 V 4 C 4 3 4.5 2.5 5 2.5 Z M 12 2.5 V 6.5 H 16"),
  ],
  sparkle: [
    path("M 8 2.5 L 9.5 7 L 14 8.5 L 9.5 10 L 8 14.5 L 6.5 10 L 2 8.5 L 6.5 7 Z"),
    path("M 15.5 12 L 16.5 14 L 18 15 L 16.5 16 L 15.5 18 L 14.5 16 L 13 15 L 14.5 14 Z"),
  ],
  flag: [
    line(4.5, 3, 4.5, 17),
    path("M 4.5 4 C 8 2.5 10.5 5.5 16 4 V 12 C 10.5 13.5 8 10.5 4.5 12"),
  ],
  branch: [
    dot(10, 3.5), dot(5.5, 16), dot(14.5, 16),
    path("M 10 5.5 V 7 C 10 10 5.5 10.5 5.5 13.5 M 10 7 C 10 10 14.5 10.5 14.5 13.5"),
  ],
  merge: [
    path("M 4.5 3.5 V 7 C 4.5 10 10 10 10 13.5 M 15.5 3.5 V 7 C 15.5 10 10 10 10 13.5 V 17"),
    polyline("7 14 10 17 13 14"),
  ],
  brand: [
    line(6.5, 7.5, 8.5, 12.5), line(13.5, 7.5, 11.5, 12.5), line(7.5, 6, 12.5, 6),
    circle(5, 6, 2.5), circle(15, 6, 2.5), circle(10, 15, 2.5),
  ],
  // Composer send is an upward action arrow, never a second glyph drawing.
  send: { alias: "arrow-up" },
} as const satisfies Record<string, GlyphDefinition>;
