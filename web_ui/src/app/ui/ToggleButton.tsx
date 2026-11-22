import { useState, type ButtonHTMLAttributes } from "react";
import { Icon, type IconSize } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import type { ButtonSize } from "./Button";
import "./toggle-button.css";

export type ToggleButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "type"> & {
  icon?: IconName;
  label?: string;
  size?: ButtonSize;
  pressed?: boolean;
  defaultPressed?: boolean;
  onPressedChange?: (pressed: boolean) => void;
};

const iconSizes: Record<ButtonSize, IconSize> = { sm: "sm", md: "md", lg: "lg" };

export function ToggleButton({
  icon, label, size = "md", pressed, defaultPressed = false, onPressedChange,
  onClick, className, ...rest
}: ToggleButtonProps) {
  const [internalPressed, setInternalPressed] = useState(defaultPressed);
  const active = pressed ?? internalPressed;

  return (
    <button
      {...rest}
      type="button"
      className={["gl-toggle-button", "gl-toggle-button--" + size, active && "gl-toggle-button--pressed", className].filter(Boolean).join(" ")}
      aria-pressed={active}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        if (pressed === undefined) setInternalPressed(!active);
        onPressedChange?.(!active);
      }}
    >
      {icon && <Icon name={icon} size={iconSizes[size]} className={active ? "gl-toggle-button-pressed-icon" : undefined} />}
      {label && <span className="gl-toggle-button-label">{label}</span>}
    </button>
  );
}
