import { createElement } from "react";
import { Icon, type IconSize } from "./icons/Icon";
import "./spinner.css";

export type SpinnerSize = 12 | 14 | 16 | 20;

export type SpinnerProps = {
  size?: SpinnerSize;
  label?: string;
  /** Hide the status from assistive technology inside an already labelled control. */
  decorative?: boolean;
  className?: string;
};

const iconSizes: Record<SpinnerSize, IconSize> = {
  12: "xs", 14: "sm", 16: "md", 20: "lg",
};

export function Spinner({ size = 16, label = "Loading", decorative = false, className }: SpinnerProps) {
  // The animated status arc uses SVG geometry; the registry's static
  // status-running glyph is the reduced-motion alternative.
  const arc = createElement("svg", {
    viewBox: "0 0 20 20",
    className: "gl-spinner-arc",
    focusable: "false",
    "aria-hidden": true,
    fill: "none",
    stroke: "currentColor",
  }, createElement("circle", {
    cx: 10, cy: 10, r: 7, strokeWidth: 2, strokeLinecap: "round",
    strokeDasharray: "30 44",
  }));

  return (
    <span
      className={["gl-spinner", "gl-spinner--" + size, className].filter(Boolean).join(" ")}
      role={decorative ? undefined : "status"}
      aria-label={decorative ? undefined : label}
      aria-hidden={decorative ? true : undefined}
    >
      {arc}
      <Icon name="status-running" size={iconSizes[size]} className="gl-spinner-static" />
    </span>
  );
}
