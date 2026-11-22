import { useId, useState, type InputHTMLAttributes } from "react";
import "./switch.css";

export type SwitchProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "size" | "checked" | "defaultChecked"> & {
  label: string;
  description?: string;
  error?: string;
  checked?: boolean;
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
};

export function Switch({
  label, description, error, checked, defaultChecked = false, onCheckedChange, onChange, disabled,
  id, className, "aria-describedby": extraDescription, ...inputProps
}: SwitchProps) {
  const generated = useId();
  const controlId = id ?? generated;
  const [uncontrolled, setUncontrolled] = useState(defaultChecked);
  const selected = checked ?? uncontrolled;
  const descriptionId = description ? controlId + "-description" : undefined;
  const errorId = error ? controlId + "-error" : undefined;

  return (
    <label className={["gl-switch", disabled && "gl-switch-disabled", className].filter(Boolean).join(" ")}>
      <span className="gl-switch-track-wrap">
        <input
          {...inputProps}
          id={controlId}
          type="checkbox"
          role="switch"
          className="gl-switch-input"
          checked={selected}
          disabled={disabled}
          aria-checked={selected}
          aria-invalid={error ? true : inputProps["aria-invalid"]}
          aria-describedby={[extraDescription, descriptionId, errorId].filter(Boolean).join(" ") || undefined}
          onChange={(event) => {
            if (checked === undefined) setUncontrolled(event.currentTarget.checked);
            onCheckedChange?.(event.currentTarget.checked);
            onChange?.(event);
          }}
        />
        <span className="gl-switch-track" aria-hidden="true"><span className="gl-switch-knob" /></span>
      </span>
      <span className="gl-switch-copy">
        <span className="gl-switch-label">{label}</span>
        {description && <span className="gl-switch-description" id={descriptionId}>{description}</span>}
        {error && <span className="gl-switch-error" id={errorId}>{error}</span>}
      </span>
    </label>
  );
}
