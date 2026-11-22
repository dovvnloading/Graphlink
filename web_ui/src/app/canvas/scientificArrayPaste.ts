import { parseInputs, type ScienceField, type ScienceLimits } from "./scientificWorkbench";

const numericCell = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:\s*([^0-9.\s].*))?$/;

function jsonNumber(token: string): string {
  return token.replace(/^\+/, "").replace(/^(-?)0+(?=\d)/, "$1")
    .replace(/^(-?)\./, (_, sign: string) => sign + "0.").replace(/\.(?=$|[eE])/, ".0");
}

export function pastedArray(text: string, field: ScienceField, limits?: ScienceLimits): string {
  const clean = text.replace(/(?:\r?\n)+$/, "");
  const maxCells = field.kind === "matrix" ? (limits?.cells ?? 4096) : (limits?.points ?? 1024);
  // The server accepts up to 256 characters per measurement, plus a delimiter.
  if (!clean || clean.length > maxCells * 257) throw new Error("Paste a bounded numeric row, column or rectangular grid.");
  const delimiter = clean.includes("\t") ? "\t" : ",";
  const rows = clean.split(/\r\n?|\n/).map((line) => line.split(delimiter));
  const width = rows[0].length;
  if (rows.length > (limits?.points ?? 1024) || (field.kind === "matrix" &&
      (width > (limits?.columns ?? 32) || rows.length * width > (limits?.cells ?? 4096)))) {
    throw new Error("The pasted grid exceeds the operation's input limits.");
  }
  if (field.kind === "vector" && rows.length > 1 && width !== 1) {
    throw new Error("Paste one row or one column for a vector.");
  }
  const encoded = rows.map((row, r) => {
    if (row.length !== width) throw new Error(`Row ${r + 1} has ${row.length} cells. Expected ${width}.`);
    return row.map((input, c) => {
      const label = `Row ${r + 1}, column ${c + 1}`;
      const token = input.trim();
      const match = numericCell.exec(token);
      if (!match || /^[eE][+-]?\d*$/.test(match[2] ?? "")) throw new Error(`${label} needs a numeric measurement.`);
      const raw = match[2] ? JSON.stringify(token) : jsonNumber(match[1]);
      const cell = { ...field, label, kind: "number" as const };
      parseInputs({ id: "", title: "", description: "", fields: [cell], example: {} },
        { [cell.key]: match[2] ? token : raw }, limits);
      return raw;
    });
  });
  const raw = field.kind === "vector" ? "[" + encoded.flat().join(",") + "]" :
    "[" + encoded.map((row) => "[" + row.join(",") + "]").join(",") + "]";
  parseInputs({ id: "", title: "", description: "", fields: [field], example: {} }, { [field.key]: raw }, limits);
  return raw;
}
