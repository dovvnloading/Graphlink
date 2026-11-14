import type { CSSProperties } from "react";

/** Saved colour choices use CSS hex; leave imported non-hex values alone.
 * Choose the higher-contrast ink against the swatch's sRGB luminance. */
export function swatchStyle(color: string | null): CSSProperties | undefined {
  if (!color) return;
  const style: CSSProperties = { backgroundColor: color };
  const match = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(color);
  if (!match) return style;
  const hex = match[1].length === 3 ? [...match[1]].map((c) => c + c).join("") : match[1];
  const channel = (offset: number) => {
    const value = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4);
  return { ...style, "--node-swatch-ink": luminance > 0.179 ? "black" : "white" } as CSSProperties;
}
