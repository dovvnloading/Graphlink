import { useState, type InputHTMLAttributes, type KeyboardEvent } from "react";
import { Field } from "./Field";
import { Icon } from "./icons/Icon";
import "./numberField.css";

export type NumberFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "size" | "min" | "max" | "step" | "value" | "defaultValue"> & {
  label: string;
  description?: string;
  error?: string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  value?: number | "";
  defaultValue?: number | "";
  onValueChange?: (value: number | "") => void;
};

export function NumberField({
  label, description, error, min, max, step = 1, unit, value, defaultValue = 0,
  onValueChange, onChange, onBlur, onKeyDown, id, className, disabled, readOnly, ...inputProps
}: NumberFieldProps) {
  const [internal, setInternal] = useState<number | "">(defaultValue);
  const [draft, setDraft] = useState<string | null>(null);
  const current = value === undefined ? internal : value;
  const increment = Number.isFinite(step) && step > 0 ? step : 1;
  const lower = min ?? -Infinity;
  const upper = max ?? Infinity;
  const number = current === "" ? NaN : current;

  function clamp(next: number) {
    const base = min ?? 0;
    const stepped = base + Math.round((next - base) / increment) * increment;
    return Number(Math.min(upper, Math.max(lower, stepped)).toFixed(10));
  }
  function emit(next: number | "") {
    setDraft(null);
    if (value === undefined) setInternal(next);
    onValueChange?.(next);
  }
  function adjust(direction: number) {
    const editing = draft === null || draft.trim() === "" ? number : Number(draft);
    const start = Number.isFinite(editing) ? editing : min ?? 0;
    emit(clamp(start + direction * increment));
  }
  function handleKeys(event: KeyboardEvent<HTMLInputElement>) {
    const direction = event.key === "ArrowUp" ? 1 : event.key === "ArrowDown" ? -1 :
      event.key === "PageUp" ? 10 : event.key === "PageDown" ? -10 : 0;
    if (direction !== 0 && !disabled && !readOnly) {
      event.preventDefault();
      adjust(direction);
    }
    onKeyDown?.(event);
  }

  return (
    <Field label={label} description={description} error={error} id={id}>
      {(control) => (
        <div className={["gl-number-field", disabled && "gl-number-field-disabled", className].filter(Boolean).join(" ")}>
          <button type="button" className="gl-number-step" aria-label={"Decrease " + label}
            disabled={disabled || readOnly || (Number.isFinite(number) && number <= lower)}
            onClick={() => adjust(-1)}><Icon name="minus" size="sm" /></button>
          <input
            {...inputProps}
            {...control}
            className="gl-number-field-input"
            type="number"
            min={min}
            max={max}
            step={increment}
            disabled={disabled}
            readOnly={readOnly}
            value={draft ?? current}
            onChange={(event) => {
              setDraft(event.currentTarget.value);
              onChange?.(event);
            }}
            onKeyDown={handleKeys}
            onBlur={(event) => {
              if (draft !== null) {
                const parsed = draft.trim() === "" ? "" : Number(draft);
                emit(parsed === "" || !Number.isFinite(parsed) ? "" : clamp(parsed));
              }
              onBlur?.(event);
            }}
          />
          {unit && <span className="gl-number-unit">{unit}</span>}
          <button type="button" className="gl-number-step" aria-label={"Increase " + label}
            disabled={disabled || readOnly || (Number.isFinite(number) && number >= upper)}
            onClick={() => adjust(1)}><Icon name="plus" size="sm" /></button>
        </div>
      )}
    </Field>
  );
}
