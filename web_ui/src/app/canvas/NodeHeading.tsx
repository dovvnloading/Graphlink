import type { CSSProperties, ReactNode } from "react";
import { Icon } from "../ui/icons/Icon";
import type { IconName } from "../ui/icons/registry";

const GLYPH_ICONS = {
  code: "node-code",
  document: "node-document",
  image: "node-image",
  chart: "node-chart",
  media: "node-media",
  frame: "node-frame",
  container: "node-container",
} as const satisfies Record<string, IconName>;

export type NodeGlyphName = keyof typeof GLYPH_ICONS;

/** Compatibility wrapper for node headings that already choose a glyph. */
export function NodeGlyph({ name }: { name: NodeGlyphName }) {
  return <span className="node-glyph" data-kind={name}><Icon name={GLYPH_ICONS[name]} size="sm" /></span>;
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
      <div className={"node-heading-row " + (className ?? "")}>{children}</div>
      {detail && <div className="node-heading-detail">{detail}</div>}
    </div>
  );
}
