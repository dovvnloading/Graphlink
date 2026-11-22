import { useCallback, useLayoutEffect, useRef, useState, type TextareaHTMLAttributes } from "react";
import { Field } from "./Field";
import "./textArea.css";

export type TextAreaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "rows" | "value" | "defaultValue"> & {
  label: string;
  description?: string;
  error?: string;
  minRows?: number;
  maxRows?: number;
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
};

export function TextArea({
  label, description, error, minRows = 2, maxRows = 8, value, defaultValue = "",
  onValueChange, onChange, id, className, disabled, ...inputProps
}: TextAreaProps) {
  const [internal, setInternal] = useState(defaultValue);
  const element = useRef<HTMLTextAreaElement>(null);
  const current = value ?? internal;
  const min = Math.max(1, minRows);
  const max = Math.max(min, maxRows);

  const resize = useCallback(() => {
    const node = element.current;
    if (!node) return;
    node.style.height = "auto";
    const styles = window.getComputedStyle(node);
    const line = parseFloat(styles.lineHeight) || 18;
    const padding = (parseFloat(styles.paddingTop) || 0) + (parseFloat(styles.paddingBottom) || 0);
    const border = (parseFloat(styles.borderTopWidth) || 0) + (parseFloat(styles.borderBottomWidth) || 0);
    const minHeight = line * min + padding + border;
    const maxHeight = line * max + padding + border;
    const desired = node.scrollHeight ? node.scrollHeight + border : minHeight;
    node.style.height = Math.min(maxHeight, Math.max(minHeight, desired)) + "px";
    node.style.overflowY = desired > maxHeight ? "auto" : "hidden";
  }, [min, max]);

  useLayoutEffect(() => { resize(); }, [current, resize]);

  return (
    <Field label={label} description={description} error={error} id={id}>
      {(control) => (
        <textarea {...inputProps} {...control} ref={element} className={["gl-text-area", className].filter(Boolean).join(" ")}
          rows={min} value={current} disabled={disabled}
          onChange={(event) => {
            if (value === undefined) setInternal(event.currentTarget.value);
            onValueChange?.(event.currentTarget.value);
            onChange?.(event);
            resize();
          }} />
      )}
    </Field>
  );
}
