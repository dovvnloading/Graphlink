import type { ButtonHTMLAttributes } from "react";
import { Icon } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import type { ButtonSize, ButtonVariant } from "./Button";
import "./icon-button.css";

export type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "type" | "title" | "aria-label"> & {
  icon: IconName;
  /** Required accessible name; the visual Tooltip is attached in a later task. */
  label: string;
  /** Reserved for the future Kbd tooltip; exposed as aria-keyshortcuts now. */
  shortcut?: string;
  size?: ButtonSize;
  variant?: ButtonVariant;
};

export function IconButton({
  icon, label, shortcut, size = "md", variant = "ghost", className, ...rest
}: IconButtonProps) {
  if (import.meta.env.DEV && !(label ?? "").trim()) {
    console.error("IconButton requires a non-empty label.");
  }

  return (
    <button
      {...rest}
      type="button"
      className={["gl-icon-button", "gl-icon-button--" + size, "gl-icon-button--" + variant, className].filter(Boolean).join(" ")}
      aria-label={label}
      aria-keyshortcuts={shortcut}
    >
      <Icon name={icon} size={size === "sm" ? "sm" : size === "lg" ? "lg" : "md"} />
    </button>
  );
}
