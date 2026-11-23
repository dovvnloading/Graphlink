import { formatCount } from "./format";
import "./progress-bar.css";

export type ProgressBarProps = {
  label: string;
  value?: number;
  max?: number;
  indeterminate?: boolean;
  className?: string;
};

export function ProgressBar({ label, value = 0, max = 100, indeterminate = false, className }: ProgressBarProps) {
  const upper = Math.max(1, Number.isFinite(max) ? max : 1);
  const amount = Math.min(upper, Math.max(0, Number.isFinite(value) ? value : 0));
  const count = formatCount(amount) + " of " + formatCount(upper);
  return (
    <div className={["gl-progress", className].filter(Boolean).join(" ")}>
      <div className="gl-progress-heading">
        <span>{label}</span>
        {!indeterminate && <span>{count}</span>}
      </div>
      <div role="progressbar" aria-label={label}
        aria-valuemin={0} aria-valuemax={indeterminate ? undefined : upper}
        aria-valuenow={indeterminate ? undefined : amount}
        aria-valuetext={indeterminate ? undefined : count}
        className={["gl-progress-track", indeterminate && "gl-progress-track--indeterminate"].filter(Boolean).join(" ")}>
        <div className="gl-progress-fill" style={indeterminate ? undefined : { width: amount / upper * 100 + "%" }} />
      </div>
    </div>
  );
}
