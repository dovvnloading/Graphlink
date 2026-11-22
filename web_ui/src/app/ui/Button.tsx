import { useId, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Icon, type IconSize } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import { Spinner, type SpinnerSize } from "./Spinner";
import "./button.css";

export type ButtonVariant = "primary" | "secondary" | "ghost";
export type ButtonSize = "sm" | "md" | "lg";

export type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "title"> & {
  children: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconStart?: IconName;
  iconEnd?: IconName;
  loading?: boolean;
  disabledReason?: string;
  destructive?: boolean;
  fullWidth?: boolean;
  type?: "button" | "submit";
};

const iconSizes: Record<ButtonSize, IconSize> = { sm: "sm", md: "md", lg: "lg" };
const spinnerSizes: Record<ButtonSize, SpinnerSize> = { sm: 14, md: 16, lg: 20 };

export function Button({
  children,
  variant = "secondary",
  size = "md",
  iconStart,
  iconEnd,
  loading = false,
  disabled = false,
  disabledReason,
  destructive = false,
  fullWidth = false,
  type = "button",
  className,
  ...rest
}: ButtonProps) {
  const reasonId = useId();
  const reasonVisibleToAT = disabled && Boolean(disabledReason);
  const describedBy = [rest["aria-describedby"], reasonVisibleToAT ? reasonId : undefined].filter(Boolean).join(" ") || undefined;
  const leadingIcon = destructive ? "trash" : iconStart;
  const loadingWithoutLeadingIcon = loading && !leadingIcon;

  return (
    <>
      <button
        {...rest}
        type={type}
        className={[
          "gl-button", "gl-button--" + variant, "gl-button--" + size,
          fullWidth && "gl-button--full-width", className,
        ].filter(Boolean).join(" ")}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        aria-describedby={describedBy}
        data-destructive={destructive ? "true" : undefined}
      >
        {(loading || leadingIcon) && (
          <span className={loadingWithoutLeadingIcon ? "gl-button-loading-indicator" : "gl-button-icon"}>
            {loading
              ? <Spinner size={spinnerSizes[size]} decorative />
              : <Icon name={leadingIcon as IconName} size={iconSizes[size]} />}
          </span>
        )}
        <span className={loadingWithoutLeadingIcon ? "gl-button-label gl-button-label--loading" : "gl-button-label"}>{children}</span>
        {iconEnd && <span className="gl-button-icon"><Icon name={iconEnd} size={iconSizes[size]} /></span>}
      </button>
      {reasonVisibleToAT && <span id={reasonId} className="gl-button-disabled-reason">{disabledReason}</span>}
    </>
  );
}
