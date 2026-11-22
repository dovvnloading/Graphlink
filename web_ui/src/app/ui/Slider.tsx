import { useState, type InputHTMLAttributes, type KeyboardEvent } from "react";
import { Field } from "./Field";
import "./slider.css";

export type SliderTick = { value: number; label: string };
export type SliderProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "size" | "min" | "max" | "step" | "value" | "defaultValue"> & {
  label: string;
  description?: string;
  error?: string;
  min?: number;
  max?: number;
  step?: number;
  value?: number;
  defaultValue?: number;
  onValueChange?: (value: number) => void;
  ticks?: readonly SliderTick[];
  showValue?: boolean;
  formatValue?: (value: number) => string;
};

export function Slider({
  label, description, error, min = 0, max = 100, step = 1, value, defaultValue,
  onValueChange, onChange, onKeyDown, ticks, showValue = false, formatValue = String,
  id, className, disabled, ...inputProps
}: SliderProps) {
  const [internal, setInternal] = useState(defaultValue ?? min);
  const current = value ?? internal;
  const percent = (number: number) => max === min ? 0 : Math.max(0, Math.min(100, 100 * (number - min) / (max - min)));
  function update(next: number) {
    const clamped = Number(Math.max(min, Math.min(max, next)).toFixed(10));
    if (value === undefined) setInternal(clamped);
    onValueChange?.(clamped);
  }
  function handleKeys(event: KeyboardEvent<HTMLInputElement>) {
    const amount = step > 0 ? step : 1;
    const delta = event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 :
      event.key === "ArrowLeft" || event.key === "ArrowDown" ? -1 :
      event.key === "PageUp" ? 10 : event.key === "PageDown" ? -10 : 0;
    if (!disabled && (delta !== 0 || event.key === "Home" || event.key === "End")) {
      event.preventDefault();
      update(event.key === "Home" ? min : event.key === "End" ? max : current + delta * amount);
    }
    onKeyDown?.(event);
  }
  return (
    <Field label={label} description={description} error={error} id={id}>
      {(control) => (
        <div className={["gl-slider", disabled && "gl-slider-disabled", className].filter(Boolean).join(" ")}>
          <div className="gl-slider-row">
            <div className="gl-slider-rail">
              <div className="gl-slider-track" aria-hidden="true">
                <span className="gl-slider-fill" style={{ width: percent(current) + "%" }} />
              </div>
              <input
                {...inputProps}
                {...control}
                className="gl-slider-input"
                type="range"
                min={min}
                max={max}
                step={step}
                value={current}
                disabled={disabled}
                onChange={(event) => {
                  update(Number(event.currentTarget.value));
                  onChange?.(event);
                }}
                onKeyDown={handleKeys}
              />
            </div>
            {showValue && <output className="gl-slider-value" htmlFor={control.id}>{formatValue(current)}</output>}
          </div>
          {ticks && ticks.length > 0 && (
            <div className="gl-slider-ticks" aria-hidden="true">
              {ticks.map((tick) => (
                <span className="gl-slider-tick" key={tick.value} style={{ left: percent(tick.value) + "%" }}>{tick.label}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </Field>
  );
}
