import { useId, useLayoutEffect, useRef, type InputHTMLAttributes } from "react";
import { Icon } from "./icons/Icon";
import "./checkbox.css";

export type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "size"> & {
  label: string;
  description?: string;
  error?: string;
  indeterminate?: boolean;
};

export function Checkbox({
  label, description, error, indeterminate = false, id, className, disabled,
  "aria-describedby": extraDescription, ...inputProps
}: CheckboxProps) {
  const generatedId = useId();
  const controlId = id ?? generatedId;
  const inputRef = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => { if (inputRef.current) inputRef.current.indeterminate = indeterminate; }, [indeterminate]);
  const labelId = controlId + "-label";
  const descriptionId = description ? controlId + "-description" : undefined;
  const errorId = error ? controlId + "-error" : undefined;
  const describedBy = [extraDescription, descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <label className={["gl-checkbox", disabled && "gl-checkbox-disabled", className].filter(Boolean).join(" ")}>
      <span className="gl-checkbox-control">
        <input
          {...inputProps}
          ref={inputRef}
          id={controlId}
          type="checkbox"
          className="gl-checkbox-input"
          disabled={disabled}
          aria-labelledby={labelId}
          aria-checked={indeterminate ? "mixed" : undefined}
          aria-describedby={describedBy}
          aria-invalid={error ? true : inputProps["aria-invalid"]}
        />
        <span className="gl-checkbox-box" aria-hidden="true">
          <Icon name={indeterminate ? "minus" : "check"} size="sm" />
        </span>
      </span>
      <span className="gl-checkbox-copy">
        <span className="gl-checkbox-label" id={labelId}>{label}</span>
        {description && <span className="gl-checkbox-description" id={descriptionId}>{description}</span>}
        {error && <span className="gl-checkbox-error" id={errorId}><Icon name="status-error" size="sm" />{error}</span>}
      </span>
    </label>
  );
}
