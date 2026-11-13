import type { CSSProperties, ReactNode } from "react";

// Distinct silhouettes keep kinds recognizable at overview zoom and without colour.
const GLYPHS = {
  user: (
    <>
      <circle cx="10" cy="6.5" r="2.8" />
      <path d="M4.5 16c.4-3 2.4-4.5 5.5-4.5s5.1 1.5 5.5 4.5" />
    </>
  ),
  assistant: (
    <>
      <path d="m9.5 2.8 1.6 4.7 4.6 1.7-4.6 1.7-1.6 4.7-1.7-4.7-4.6-1.7 4.6-1.7Z" />
      <path d="M15.7 12.9v3.4M14 14.6h3.4" />
    </>
  ),
  conversation: (
    <>
      <path d="M3 3.5h11v8H7l-4 3z" />
      <path d="M8.5 14.5H13l4 2.5V8.5" />
    </>
  ),
  thinking: (
    <>
      <path d="M6.5 12.4a5.2 5.2 0 1 1 7 0c-1 .8-1.3 1.3-1.3 2.1H7.8c0-.8-.3-1.3-1.3-2.1Z" />
      <path d="M8.4 17h3.2" />
    </>
  ),
  code: <path d="m6 6-4 4 4 4m8-8 4 4-4 4m-3-10-2 12" />,
  document: (
    <>
      <path d="M5 2.8h6.4l3.6 3.6V17H5zM11 3v4h4" />
      <path d="M7.8 10.2h4.4m-4.4 3h4.4" />
    </>
  ),
  note: (
    <>
      <path d="M3.7 3.5h12.6v8.8l-4 4.2H3.7z" />
      <path d="M12 16.3v-4.2h4.2M6.7 7.5h6.2" />
    </>
  ),
  artifact: <path d="m10 2.7 7 4v7.2l-7 4-7-4V6.7zM3.5 7l6.5 3.8L16.5 7M10 10.8v6.6" />,
  image: (
    <>
      <rect x="2.8" y="3.8" width="14.4" height="12.4" rx="2" />
      <circle cx="7" cy="7.8" r="1" />
      <path d="m4.7 14 3.2-3.1 2.1 2.2 3.3-4.1 3.6 4.6" />
    </>
  ),
  chart: <path d="M3.2 3.5v12.8h14M7 12.8V9.5m4 3.3V5.8m4 7v-5" />,
  html: (
    <>
      <rect x="2.5" y="3.5" width="15" height="13" rx="2" />
      <path d="M3 7.2h14m-9.2 2.4-2 2 2 2m4.4-4 2 2-2 2" />
    </>
  ),
  review: (
    <>
      <path d="M8.5 15.8H4.3V3h7l3.4 3.4v2M11 3v4h3.5" />
      <circle cx="12.4" cy="12.6" r="2.7" />
      <path d="m14.3 14.6 2.7 2.6" />
    </>
  ),
  sandbox: (
    <>
      <rect x="2.5" y="4" width="15" height="12" rx="2" />
      <path d="m5.7 7.5 2.8 2.5-2.8 2.5m5.1 0h3.3" />
    </>
  ),
  gitlink: (
    <>
      <circle cx="5.5" cy="4.5" r="1.8" />
      <circle cx="5.5" cy="15.5" r="1.8" />
      <circle cx="14.5" cy="5.5" r="1.8" />
      <path d="M5.5 6.3v7.4m.1-1.5c5 0 8.9-1 8.9-4.9" />
    </>
  ),
  research: (
    <>
      <circle cx="8.4" cy="8.4" r="5.2" />
      <path d="m12.3 12.3 4.2 4.2M5.8 8.4H11m-2.6-2.6v5.2" />
    </>
  ),
  plan: <path d="m3 5.6 1.1 1.2 2.1-2.6M9 5.5h7.5M3 10l1.1 1.2 2.1-2.6M9 10h7.5M3 14.5l1.1 1.2 2.1-2.6M9 14.5h7.5" />,
  harness: (
    <>
      <rect x="3.5" y="5.7" width="13" height="10.7" rx="3" />
      <path d="M10 3v2.6m-3.1 4.2v1.5m6.2-1.5v1.5M8 13.7h4" />
    </>
  ),
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
  glyph: NodeGlyphName;
  children: ReactNode;
  className?: string;
  detail?: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div className="scene-node-title node-heading" style={style}>
      <span className="node-heading-icon"><NodeGlyph name={glyph} /></span>
      <div className={`node-heading-row ${className ?? ""}`}>{children}</div>
      {detail && <div className="node-heading-detail">{detail}</div>}
    </div>
  );
}
