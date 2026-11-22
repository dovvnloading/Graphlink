import type { GlyphDefinition, GlyphPrimitive } from "../registry";

// Match the chrome glyphs: half-unit-snapped geometry in the 20-unit viewBox.
// Stroke, colour and sizing are owned by Icon.tsx, not by individual glyphs.
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

const chatBubble = path(
  "M 5 4 H 15 C 16.5 4 17 5 17 6.5 V 12.5 C 17 14 16 15 14.5 15 H 8 L 4 17 V 14 " +
  "C 3 13.5 3 12.5 3 11.5 V 6.5 C 3 5 3.5 4 5 4 Z",
);
const fileFrame = path(
  "M 5 2.5 H 12 L 16 6.5 V 16 C 16 17 15.5 17.5 14.5 17.5 H 5.5 " +
  "C 4.5 17.5 4 17 4 16 V 4 C 4 3 4.5 2.5 5 2.5 Z M 12 2.5 V 6.5 H 16",
);
const chartAxes = path("M 3.5 3.5 V 16.5 H 17");

export const featureGlyphs = {
  chat: [chatBubble, line(6.5, 8, 13.5, 8), line(6.5, 11.5, 11.5, 11.5)],
  "chat-plus": [chatBubble, line(10, 7, 10, 13), line(7, 10, 13, 10)],
  workspace: [
    rect(2.5, 2.5, 15, 15),
    rect(4.5, 4.5, 4, 4), rect(11.5, 4.5, 4, 4),
    rect(4.5, 11.5, 4, 4), rect(11.5, 11.5, 4, 4),
  ],
  cloud: [path(
    "M 6 15.5 H 15 C 17 15.5 18 14 18 12 C 18 10 16.5 8.5 14.5 8.5 " +
    "C 14 5.5 11.5 3.5 8.5 4 C 6 4 4.5 6 4.5 8.5 " +
    "C 2.5 9 2 10.5 2 12 C 2 14 3.5 15.5 6 15.5 Z",
  )],
  cpu: [
    rect(6, 6, 8, 8),
    line(8, 3, 8, 6), line(12, 3, 12, 6),
    line(8, 14, 8, 17), line(12, 14, 12, 17),
    line(3, 8, 6, 8), line(3, 12, 6, 12),
    line(14, 8, 17, 8), line(14, 12, 17, 12),
  ],
  server: [
    rect(3, 3, 14, 5.5), rect(3, 11.5, 14, 5.5),
    dot(6.5, 5.5), dot(6.5, 14),
  ],
  key: [circle(6.5, 8, 3.5), path("M 10 8 H 17 V 11 H 14.5 V 13.5 H 12")],
  plug: [
    path("M 6 7.5 H 14 V 10 C 14 13 12.5 14.5 10 14.5 C 7.5 14.5 6 13 6 10 Z"),
    line(8, 3.5, 8, 7.5), line(12, 3.5, 12, 7.5), line(10, 14.5, 10, 17.5),
  ],
  puzzle: [path(
    "M 3.5 4 H 7.5 C 7.5 2.5 8.5 2 10 2 C 11.5 2 12.5 2.5 12.5 4 H 16 V 8 " +
    "C 17.5 8 18 9 18 10.5 C 18 12 17.5 13 16 13 V 16.5 H 12 " +
    "C 12 15 11 14 9.5 14 C 8 14 7 15 7 16.5 H 3.5 V 13 " +
    "C 5 13 6 12 6 10.5 C 6 9 5 8 3.5 8 Z",
  )],
  hub: [
    line(10, 6, 10, 8.5), line(5.5, 14, 8.5, 11), line(11.5, 11, 14.5, 14),
    dot(10, 4.5), dot(4, 15.5), dot(16, 15.5), dot(10, 10),
  ],
  shield: [path(
    "M 10 2.5 L 16.5 5 V 10 C 16.5 14 13.5 16.5 10 18 " +
    "C 6.5 16.5 3.5 14 3.5 10 V 5 Z",
  )],
  coins: [
    path("M 3 6 C 3 2.5 17 2.5 17 6 C 17 9.5 3 9.5 3 6 Z"),
    path("M 3 13.5 C 3 10 17 10 17 13.5 C 17 17 3 17 3 13.5 Z"),
  ],
  palette: [
    path("M 10 2.5 C 5 2.5 2.5 6 2.5 10 C 2.5 14.5 6 17.5 10.5 17.5 " +
      "C 12.5 17.5 13.5 16 13 14.5 C 12.5 13 13.5 11.5 15 11.5 H 16 " +
      "C 17.5 11.5 18 10 17.5 8 C 16.5 4.5 13.5 2.5 10 2.5 Z"),
    dot(6.5, 7), dot(11, 5.5), dot(6.5, 12.5),
  ],
  bell: [
    path("M 4 14.5 L 5.5 12 V 8 C 5.5 2 14.5 2 14.5 8 V 12 L 16 14.5 Z"),
    line(3.5, 15.5, 16.5, 15.5), dot(10, 17),
  ],
  database: [
    path("M 3 5.5 C 3 2 17 2 17 5.5 C 17 9 3 9 3 5.5 Z"),
    path("M 3 5.5 V 14.5 C 3 18 17 18 17 14.5 V 5.5"),
    path("M 3 10 C 3 13.5 17 13.5 17 10"),
  ],

  "chart-line": [chartAxes, polyline("5.5 13 9 10 12 11.5 16 5.5")],
  "chart-bar": [
    chartAxes,
    rect(5, 11.5, 2.5, 4), rect(10, 8.5, 2.5, 7), rect(15, 5, 2.5, 10.5),
  ],
  "chart-pie": [circle(10, 10, 7.5), path("M 10 2.5 V 10 H 17.5")],
  "chart-scatter": [chartAxes, dot(6, 13), dot(9, 9), dot(12, 12), dot(15, 5)],
  "chart-box": [
    rect(6, 6, 8, 8), line(10, 6, 10, 14),
    line(3, 10, 6, 10), line(14, 10, 17, 10),
    line(3, 7.5, 3, 12.5), line(17, 7.5, 17, 12.5),
  ],
  "chart-heatmap": [
    // Nine square cells, with a second outline around the central cell.
    path("M 2 2 H 4 V 4 H 2 Z M 9 2 H 11 V 4 H 9 Z M 16 2 H 18 V 4 H 16 Z " +
      "M 2 9 H 4 V 11 H 2 Z M 9 9 H 11 V 11 H 9 Z M 16 9 H 18 V 11 H 16 Z " +
      "M 2 16 H 4 V 18 H 2 Z M 9 16 H 11 V 18 H 9 Z M 16 16 H 18 V 18 H 16 Z"),
    path("M 6.5 6.5 H 13.5 V 13.5 H 6.5 Z"),
  ],
  "chart-sankey": [
    path("M 3 3.5 C 8 3.5 12 5.5 17 5.5"),
    path("M 3 10 C 8 10 12 10 17 10"),
    path("M 3 16.5 C 8 16.5 12 14.5 17 14.5"),
  ],

  ruler: [
    rect(2.5, 6, 15, 8),
    line(6, 6, 6, 10), line(10, 6, 10, 12), line(14, 6, 14, 10),
  ],
  crosshair: [
    circle(10, 10, 5),
    line(10, 2.5, 10, 5), line(10, 15, 10, 17.5),
    line(2.5, 10, 5, 10), line(15, 10, 17.5, 10),
    dot(10, 10),
  ],
  video: [rect(2.5, 4, 15, 12), polyline("8 7 13 10 8 13 8 7")],
  audio: [
    line(4, 9, 4, 11), line(7, 6.5, 7, 13.5), line(10, 3.5, 10, 16.5),
    line(13, 6.5, 13, 13.5), line(16, 9, 16, 11),
  ],
  "grid-dots": [
    dot(4.5, 4.5), dot(10, 4.5), dot(15.5, 4.5),
    dot(4.5, 10), dot(10, 10), dot(15.5, 10),
    dot(4.5, 15.5), dot(10, 15.5), dot(15.5, 15.5),
  ],
  "grid-lines": [
    line(6.5, 3, 6.5, 17), line(13.5, 3, 13.5, 17),
    line(3, 6.5, 17, 6.5), line(3, 13.5, 17, 13.5),
  ],
  "grid-cross": [
    path("M 6 4 V 8 M 4 6 H 8 M 14 4 V 8 M 12 6 H 16"),
    path("M 6 12 V 16 M 4 14 H 8 M 14 12 V 16 M 12 14 H 16"),
  ],
  magnet: [path(
    "M 4 3 V 11 C 4 17 16 17 16 11 V 3 H 12.5 V 11 " +
    "C 12.5 13.5 7.5 13.5 7.5 11 V 3 Z",
  )],
  guides: [
    path("M 10 2.5 V 6 M 10 8.5 V 11.5 M 10 14 V 17.5"),
    path("M 2.5 10 H 6 M 8.5 10 H 11.5 M 14 10 H 17.5"),
  ],
  "route-curved": [
    path("M 4.5 4.5 C 13.5 4.5 6.5 15.5 15.5 15.5"),
    dot(4.5, 4.5), dot(15.5, 15.5),
  ],
  "route-orthogonal": [
    path("M 4.5 4.5 H 10 V 15.5 H 15.5"),
    dot(4.5, 4.5), dot(15.5, 15.5),
  ],
  "text-cursor": [line(10, 3, 10, 17), line(6, 3, 14, 3), line(6, 17, 14, 17)],
  bookmark: [path("M 5 2.5 H 15 V 17.5 L 10 14 L 5 17.5 Z")],
  hourglass: [path(
    "M 5 3 H 15 V 5.5 L 10 10 L 15 14.5 V 17 H 5 V 14.5 L 10 10 L 5 5.5 Z",
  )],
  "file-code": [
    fileFrame,
    polyline("8.5 9 6.5 11 8.5 13"), polyline("11.5 9 13.5 11 11.5 13"),
  ],
  "file-table": [
    fileFrame, rect(6.5, 9, 7, 6), line(10, 9, 10, 15), line(6.5, 12, 13.5, 12),
  ],
  "file-image": [
    fileFrame, dot(8, 9.5), polyline("6.5 14.5 9.5 11.5 11 13 12.5 11 14 12.5"),
  ],
  // UI-06 supplies node-web, node-science, node-image and node-model. The registry
  // resolves aliases eagerly and throws for missing targets, so defer all five
  // cross-batch aliases until those glyphs are available on main.
} as const satisfies Record<string, GlyphDefinition>;
