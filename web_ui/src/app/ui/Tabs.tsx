import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import "./tabs.css";

export type TabItem = {
  id: string;
  label: string;
  content: ReactNode;
  disabled?: boolean;
};

export type TabsProps = {
  label: string;
  items: readonly TabItem[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (id: string) => void;
  className?: string;
};

export function Tabs({ label, items, value, defaultValue, onValueChange, className }: TabsProps) {
  const generated = useId();
  const [internal, setInternal] = useState(defaultValue ?? items.find((item) => !item.disabled)?.id);
  const current = value === undefined ? internal : value;
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const selected = items.find((item) => item.id === current && !item.disabled);
  const active = selected ?? items.find((item) => !item.disabled);

  function select(item: TabItem) {
    if (item.disabled) return;
    if (value === undefined) setInternal(item.id);
    if (item.id !== current) onValueChange?.(item.id);
  }

  function handleKeys(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const direction = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    let nextIndex = -1;
    if (event.key === "Home") nextIndex = items.findIndex((item) => !item.disabled);
    if (event.key === "End") {
      for (let i = items.length - 1; i >= 0; i -= 1) {
        if (!items[i].disabled) { nextIndex = i; break; }
      }
    }
    if (direction !== 0) {
      let candidate = index;
      for (let count = 0; count < items.length; count += 1) {
        candidate = (candidate + direction + items.length) % items.length;
        if (!items[candidate].disabled) { nextIndex = candidate; break; }
      }
    }
    if (nextIndex < 0 || (direction === 0 && event.key !== "Home" && event.key !== "End")) return;
    event.preventDefault();
    tabs.current[nextIndex]?.focus();
    select(items[nextIndex]);
  }

  return (
    <div className={["gl-tabs", className].filter(Boolean).join(" ")}>
      <div className="gl-tabs-list" role="tablist" aria-label={label} aria-orientation="horizontal">
        {items.map((item, index) => (
          <button
            key={item.id}
            ref={(node) => { tabs.current[index] = node; }}
            className="gl-tabs-tab"
            type="button"
            id={generated + "-tab-" + item.id}
            role="tab"
            aria-selected={active?.id === item.id}
            aria-controls={generated + "-panel-" + item.id}
            tabIndex={active?.id === item.id ? 0 : -1}
            disabled={item.disabled}
            onClick={() => select(item)}
            onKeyDown={(event) => handleKeys(event, index)}>
            {item.label}
          </button>
        ))}
      </div>
      {active && (
        <div className="gl-tabs-panel" key={active.id} id={generated + "-panel-" + active.id}
          role="tabpanel" aria-labelledby={generated + "-tab-" + active.id} tabIndex={0}>
          {active.content}
        </div>
      )}
    </div>
  );
}
