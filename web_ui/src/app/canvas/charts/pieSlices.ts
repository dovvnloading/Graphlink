const MAX_SLICES = 10;

interface Slice {
  label: string;
  value: number;
  startAngle: number;
  endAngle: number;
}

export function buildSlices(labels: string[], values: number[]): Slice[] {
  let entries = labels.map((label, index) => ({ label, value: values[index] }));
  if (entries.length > MAX_SLICES) {
    const ranked = [...entries].sort((a, b) => b.value - a.value);
    const top = ranked.slice(0, MAX_SLICES - 1);
    const remainder = ranked.slice(MAX_SLICES - 1).reduce((sum, entry) => sum + entry.value, 0);
    entries = [...top, { label: "Other", value: remainder }];
  }
  const total = entries.reduce((sum, entry) => sum + entry.value, 0) || 1;
  let cursor = 0;
  return entries.map((entry) => {
    const sweep = (entry.value / total) * 360;
    const slice: Slice = { label: entry.label, value: entry.value, startAngle: cursor, endAngle: cursor + sweep };
    cursor += sweep;
    return slice;
  });
}
