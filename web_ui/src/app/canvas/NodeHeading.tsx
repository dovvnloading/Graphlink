import type { CSSProperties, ReactNode } from "react";

// Distinct silhouettes keep kinds recognizable at overview zoom and without colour.
const GLYPHS = {
  code: <path d="m6 6-4 4 4 4m8-8 4 4-4 4m-3-10-2 12" />,
  document: (
    <>
      <path d="M5 2.8h6.4l3.6 3.6V17H5zM11 3v4h4" />
      <path d="M7.8 10.2h4.4m-4.4 3h4.4" />
    </>
  ),
  image: (
    <>
      <rect x="2.8" y="3.8" width="14.4" height="12.4" rx="2" />
      <circle cx="7" cy="7.8" r="1" />
      <path d="m4.7 14 3.2-3.1 2.1 2.2 3.3-4.1 3.6 4.6" />
    </>
  ),
  chart: <path d="M3.2 3.5v12.8h14M7 12.8V9.5m4 3.3V5.8m4 7v-5" />,
  media: (
    <>
      <rect x="2.5" y="4" width="15" height="12" rx="2" />
      <path d="m8 7 5 3-5 3z" />
    </>
  ),
  frame: <path d="M7 3.5H3.5V7m9-3.5h4V7M3.5 13v3.5H7m6 0h3.5V13" />,
  container: <path d="M2.8 6.5h14.4v9.7H2.8zM4.5 3.8h11" />,
};

export type NodeGlyphName = keyof typeof GLYPHS;

export function NodeGlyph({ name }: { name: NodeGlyphName }) {
  return (
    <svg
      className="node-glyph"
      data-kind={name}
      aria-hidden="true"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.55"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {GLYPHS[name]}
    </svg>
  );
}

export function NodeHeading({ glyph, children, className, detail, style }: {
  glyph?: NodeGlyphName;
  children: ReactNode;
  className?: string;
  detail?: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div className="scene-node-title node-heading" style={style}>
      {glyph && <NodeGlyph name={glyph} />}
      <div className={`node-heading-row ${className ?? ""}`}>{children}</div>
      {detail && <div className="node-heading-detail">{detail}</div>}
    </div>
  );
}
