import { useRef, useState, type InputHTMLAttributes } from "react";
import { Field } from "./Field";
import { Icon } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import "./textField.css";

export type TextFieldSuffix =
  | { type: "unit"; text: string }
  | { type: "clear" }
  | { type: "password" };

export type TextFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "size" | "prefix" | "value" | "defaultValue"> & {
  label: string;
  description?: string;
  error?: string;
  type?: "text" | "search" | "url" | "password" | "email";
  size?: "sm" | "md";
  mono?: boolean;
  prefixIcon?: IconName;
  suffix?: TextFieldSuffix;
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
};

export function TextField({
  label, description, error, type = "text", size = "md", mono = false,
  prefixIcon, suffix, value, defaultValue = "", onValueChange, onChange,
  id, className, disabled, readOnly, ...inputProps
}: TextFieldProps) {
  const [internal, setInternal] = useState(defaultValue);
  const [revealed, setRevealed] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const current = value ?? internal;
  const shownType = type === "password" && revealed ? "text" : type;

  function clear() {
    if (value === undefined) setInternal("");
    onValueChange?.("");
    inputRef.current?.focus();
  }

  return (
    <Field label={label} description={description} error={error} id={id}>
      {(control) => (
        <div className={[
          "gl-text-field", size === "sm" && "gl-text-field-sm",
          mono && "gl-text-field-mono", disabled && "gl-text-field-disabled", className,
        ].filter(Boolean).join(" ")}>
          {prefixIcon && <span className="gl-text-field-prefix"><Icon name={prefixIcon} size="md" /></span>}
          <input
            {...inputProps}
            {...control}
            ref={inputRef}
            type={shownType}
            className="gl-text-field-input"
            value={current}
            disabled={disabled}
            readOnly={readOnly}
            onChange={(event) => {
              if (value === undefined) setInternal(event.currentTarget.value);
              onValueChange?.(event.currentTarget.value);
              onChange?.(event);
            }}
          />
          {suffix?.type === "unit" && <span className="gl-text-field-unit">{suffix.text}</span>}
          {suffix?.type === "clear" && (
            <button className="gl-text-field-button" type="button" aria-label={"Clear " + label}
              disabled={disabled || readOnly || !current} onClick={clear}>
              <Icon name="close" size="sm" />
            </button>
          )}
          {suffix?.type === "password" && type === "password" && (
            <button className="gl-text-field-button" type="button"
              aria-label={(revealed ? "Hide " : "Show ") + label}
              aria-pressed={revealed} disabled={disabled}
              onClick={() => { setRevealed((show) => !show); inputRef.current?.focus(); }}>
              <Icon name={revealed ? "eye-off" : "eye"} size="sm" />
            </button>
          )}
        </div>
      )}
    </Field>
  );
}
