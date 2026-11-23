/** Presentation-only formatters. Explicit locale and optional clock keep tests deterministic. */

const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function finite(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

function roundedInteger(value: number): number {
  const rounded = Math.round(finite(value));
  return Object.is(rounded, -0) ? 0 : rounded;
}

/** Group rounded counts with a stable thousands separator. */
export function formatCount(count: number): string {
  return integer.format(roundedInteger(count));
}

/** Seconds to human-readable units, with seconds omitted once hours are shown. */
export function formatDuration(seconds: number): string {
  const amount = finite(seconds);
  const sign = amount < 0 ? "-" : "";
  const absolute = Math.abs(amount);
  if (absolute === 0) return "0 s";
  if (absolute < 1) return sign + "<1 s";

  const total = Math.round(absolute);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainingSeconds = total % 60;
  if (hours) return sign + hours + " h" + (minutes ? " " + minutes + " min" : "");
  if (minutes) return sign + minutes + " min" + (remainingSeconds ? " " + remainingSeconds + " s" : "");
  return sign + total + " s";
}

/** Convert bytes using binary 1024-based units (one decimal above bytes). */
export function formatBytes(bytes: number): string {
  const value = finite(bytes);
  const sign = value < 0 ? "-" : "";
  let amount = Math.abs(value);
  const units = ["B", "KB", "MB", "GB", "TB", "PB"] as const;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit++;
  }
  if (unit > 0 && Math.round(amount * 10) / 10 >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit++;
  }
  return sign + (unit === 0 ? integer.format(Math.round(amount)) : decimal.format(amount)) + " " + units[unit];
}

/** Compact signed token counts; omit trailing .0 and promote rounding across a unit. */
export function formatTokens(tokens: number): string {
  const value = roundedInteger(tokens);
  const sign = value < 0 ? "-" : "";
  let amount = Math.abs(value);
  if (amount < 1000) return integer.format(value);

  const units = ["", "k", "m", "b", "t"] as const;
  let unit = 0;
  while (amount >= 1000 && unit < units.length - 1) {
    amount /= 1000;
    unit++;
  }
  amount = Math.round(amount * 10) / 10;
  if (amount >= 1000 && unit < units.length - 1) {
    amount /= 1000;
    unit++;
  }
  const label = Number.isInteger(amount) ? String(amount) : amount.toFixed(1);
  return sign + label + units[unit];
}

/** Relative timestamp in milliseconds; pass now to fix the reference clock. */
export function formatRelativeTime(when: Date | number, now: Date | number = Date.now()): string {
  const timestamp = when instanceof Date ? when.getTime() : when;
  const reference = now instanceof Date ? now.getTime() : now;
  if (!Number.isFinite(timestamp) || !Number.isFinite(reference)) return "just now";
  const delta = timestamp - reference;
  const seconds = Math.abs(delta) / 1000;
  if (seconds < 60) return "just now";

  const units = [
    { seconds: 365 * 86400, label: "y" },
    { seconds: 30 * 86400, label: "mo" },
    { seconds: 7 * 86400, label: "wk" },
    { seconds: 86400, label: "d" },
    { seconds: 3600, label: "h" },
    { seconds: 60, label: "min" },
  ];
  const unit = units.find((candidate) => seconds >= candidate.seconds)!;
  const quantity = Math.round(seconds / unit.seconds);
  const content = quantity + " " + unit.label;
  return delta < 0 ? content + " ago" : "in " + content;
}
