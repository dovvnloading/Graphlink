import type { HTMLAttributes } from "react";
import "./kbd.css";

export type KbdProps = Omit<HTMLAttributes<HTMLSpanElement>, "children" | "aria-label"> & {
  shortcut: string;
};

export function Kbd({ shortcut, className, ...rest }: KbdProps) {
  const keys = shortcut.split("+").map((key) => key.trim()).filter(Boolean);
  return (
    <span
      {...rest}
      role="group"
      aria-label={shortcut}
      className={["gl-kbd", className].filter(Boolean).join(" ")}
    >
      {keys.map((key, index) => <kbd className="gl-kbd-key" key={index} aria-hidden="true">{key}</kbd>)}
    </span>
  );
}
