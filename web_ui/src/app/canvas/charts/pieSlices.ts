const MAX_SLICES = 10;

interface Slice {
  label: string;
  value: number;
  share: number;
  startAngle: number;
  endAngle: number;
}

export function buildSlices(labels: string[], values: number[]): Slice[] {
  if (!labels.length) return [];
  // Canonical pie values are positive and finite. Scale before summing so
  // neither the total nor the Other bucket can overflow the geometry.
  const scale = Math.max(...values);
  let entries = labels.map((label, index) => ({ label, value: values[index], weight: values[index] / scale }));
  if (entries.length > MAX_SLICES) {
    const ranked = [...entries].sort((a, b) => b.value - a.value);
    const top = ranked.slice(0, MAX_SLICES - 1);
    const tail = ranked.slice(MAX_SLICES - 1);
    entries = [...top, {
      label: "Other",
      value: tail.reduce((sum, entry) => sum + entry.value, 0),
      weight: tail.reduce((sum, entry) => sum + entry.weight, 0),
    }];
  }
  const total = entries.reduce((sum, entry) => sum + entry.weight, 0);
  let cursor = 0;
  return entries.map((entry, index) => {
    const share = entry.weight / total;
    const endAngle = index === entries.length - 1 ? 360 : Math.min(360, cursor + share * 360);
    const slice: Slice = { label: entry.label, value: entry.value, share, startAngle: cursor, endAngle };
    cursor = endAngle;
    return slice;
  });
}

// A positive slice may be too small for even a one-decimal percentage.
export function formatPieShare(share: number): string {
  return share < 0.001 ? "<0.1%" : `${(share * 100).toFixed(1)}%`;
}
