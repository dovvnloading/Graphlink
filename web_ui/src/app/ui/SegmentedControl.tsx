import { useRef, useState, type HTMLAttributes, type KeyboardEvent } from "react";
import { Icon } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import "./segmented-control.css";

export type SegmentOption = { value: string; label: string; icon?: IconName };
export type SegmentedControlProps = Omit<HTMLAttributes<HTMLDivElement>, "onChange" | "defaultValue"> & {
  label: string;
  options: readonly SegmentOption[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  size?: "sm" | "md";
  disabled?: boolean;
};

export function SegmentedControl({
  label, options, value, defaultValue, onChange, size = "md", disabled = false, className, ...rest
}: SegmentedControlProps) {
  const [internalValue, setInternalValue] = useState(defaultValue ?? options[0]?.value ?? "");
  const radioRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const requestedValue = value ?? internalValue;
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === requestedValue));
  const selectedValue = options[selectedIndex]?.value;

  function select(index: number) {
    const next = options[index];
    if (!next || disabled) return;
    if (next.value !== selectedValue) {
      if (value === undefined) setInternalValue(next.value);
      onChange?.(next.value);
    }
    radioRefs.current[index]?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (disabled || options.length === 0) return;
    let next: number;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        next = (index + 1) % options.length;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        next = (index - 1 + options.length) % options.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = options.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    select(next);
  }

  return (
    <div
      {...rest}
      role="radiogroup"
      aria-label={label}
      aria-disabled={disabled || undefined}
      className={["gl-segmented-control", "gl-segmented-control--" + size, className].filter(Boolean).join(" ")}
    >
      {options.map((option, index) => {
        const selected = option.value === selectedValue;
        return (
          <button
            key={option.value}
            ref={(node) => { radioRefs.current[index] = node; }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected && !disabled ? 0 : -1}
            disabled={disabled}
            className={["gl-segmented-control-segment", "gl-segmented-control-segment--" + size, selected && "gl-segmented-control-segment--selected"].filter(Boolean).join(" ")}
            onClick={() => select(index)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {option.icon && <Icon name={option.icon} size={size === "sm" ? "sm" : "md"} />}
            <span>{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
