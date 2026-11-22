import { useId, useRef, useState, type KeyboardEvent } from "react";
import { Icon } from "./icons/Icon";
import "./radioGroup.css";

export type RadioOption = {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
};

export type RadioGroupProps = {
  label: string;
  options: readonly RadioOption[];
  variant?: "plain" | "card";
  name?: string;
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
  error?: string;
  className?: string;
};

export function RadioGroup({
  label, options, variant = "plain", name, value, defaultValue, onValueChange,
  disabled = false, required, error, className,
}: RadioGroupProps) {
  const generated = useId();
  const groupName = name ?? generated;
  const [internal, setInternal] = useState(defaultValue);
  const selected = value === undefined ? internal : value;
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  const errorId = error ? generated + "-error" : undefined;
  const legendId = generated + "-legend";

  function select(next: string) {
    if (value === undefined) setInternal(next);
    if (next !== selected) onValueChange?.(next);
  }

  function onArrow(event: KeyboardEvent<HTMLInputElement>, index: number) {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 :
      event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step || disabled) return;
    event.preventDefault();
    let candidate = index;
    for (let count = 0; count < options.length; count += 1) {
      candidate = (candidate + step + options.length) % options.length;
      if (!options[candidate].disabled) {
        inputs.current[candidate]?.focus();
        select(options[candidate].value);
        break;
      }
    }
  }

  return (
    <fieldset className={["gl-radio-group", variant === "card" && "gl-radio-group-card", className].filter(Boolean).join(" ")}
      role="radiogroup" aria-labelledby={legendId} aria-describedby={errorId} aria-invalid={error ? true : undefined}
      disabled={disabled}>
      <legend className="gl-radio-legend" id={legendId}>{label}</legend>
      <div className="gl-radio-options">
        {options.map((option, index) => (
          <label className={[
            "gl-radio-option", variant === "card" && "gl-radio-card",
            selected === option.value && "gl-radio-selected",
            (disabled || option.disabled) && "gl-radio-disabled",
          ].filter(Boolean).join(" ")} key={option.value}>
            <span className="gl-radio-input-wrap">
              <input
                ref={(node) => { inputs.current[index] = node; }}
                className="gl-radio-input"
                type="radio"
                name={groupName}
                value={option.value}
                checked={selected === option.value}
                onChange={() => select(option.value)}
                onKeyDown={(event) => onArrow(event, index)}
                disabled={disabled || option.disabled}
                required={required}
                aria-invalid={error ? true : undefined}
                aria-labelledby={generated + "-option-" + index + "-label"}
                aria-describedby={[option.description ? generated + "-option-" + index + "-description" : undefined, errorId].filter(Boolean).join(" ") || undefined}
              />
              <span className="gl-radio-dot" aria-hidden="true" />
            </span>
            <span className="gl-radio-copy">
              <span className="gl-radio-label" id={generated + "-option-" + index + "-label"}>{option.label}</span>
              {option.description && <span className="gl-radio-description" id={generated + "-option-" + index + "-description"}>{option.description}</span>}
            </span>
            {variant === "card" && selected === option.value && <Icon name="check" size="md" className="gl-radio-check" />}
          </label>
        ))}
      </div>
      {error && <span className="gl-radio-error" id={errorId}><Icon name="status-error" size="sm" />{error}</span>}
    </fieldset>
  );
}
