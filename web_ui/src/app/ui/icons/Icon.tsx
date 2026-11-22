import { iconRegistry, type GlyphPrimitive, type IconName } from "./registry";
import "./icon.css";

export type IconSize = "xs" | "sm" | "md" | "lg" | "xl" | "2xl" | "3xl";

export type IconProps = {
  name: IconName;
  size?: IconSize;
  label?: string;
  className?: string;
};

function renderPrimitive(primitive: GlyphPrimitive, key: number, filledVariant: boolean) {
  switch (primitive.type) {
    case "path":
      return <path key={key} d={primitive.d} {...(filledVariant ? { fill: "currentColor", stroke: "none" } : {})} />;
    case "circle":
      return <circle key={key} cx={primitive.cx} cy={primitive.cy} r={primitive.r}
        {...(primitive.fill ? { fill: "currentColor", stroke: "none" } : {})} />;
    case "rect":
      return <rect key={key} x={primitive.x} y={primitive.y} width={primitive.width}
        height={primitive.height} rx={primitive.rx}
        {...(primitive.fill ? { fill: "currentColor", stroke: "none" } : {})} />;
    case "line":
      return <line key={key} x1={primitive.x1} y1={primitive.y1} x2={primitive.x2} y2={primitive.y2} />;
    case "polyline":
      return <polyline key={key} points={primitive.points} />;
  }
}

export function Icon({ name, size = "md", label, className }: IconProps) {
  const accessible = Boolean(label);
  return (
    <svg
      viewBox="0 0 20 20"
      className={["gl-icon", "gl-icon--" + size, className].filter(Boolean).join(" ")}
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={accessible ? undefined : "true"}
      role={accessible ? "img" : undefined}
      aria-label={accessible ? label : undefined}
    >
      {iconRegistry[name].map((primitive, key) => renderPrimitive(primitive, key, name.endsWith("-filled")))}
    </svg>
  );
}
