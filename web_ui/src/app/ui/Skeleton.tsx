import "./skeleton.css";

export type SkeletonProps = {
  variant?: "line" | "block";
  lines?: number;
  width?: string;
  height?: string;
  label?: string;
  className?: string;
};

export function Skeleton({ variant = "line", lines = 1, width, height, label, className }: SkeletonProps) {
  return (
    <div className={["gl-skeleton", className].filter(Boolean).join(" ")}
      role={label ? "status" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      {Array.from({ length: variant === "line" ? Math.max(1, Math.min(20, lines)) : 1 }, (_, index) => (
        <span key={index} aria-hidden="true"
          className={["gl-skeleton-shape", "gl-skeleton-shape--" + variant].join(" ")}
          style={{ width, height }} />
      ))}
    </div>
  );
}
