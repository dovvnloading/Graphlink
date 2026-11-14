// The server validates edits and returns canonical CSV. Decode only committed
// data here; raw file imports and pasted CSV go to the same server validator.
export function readTableCsv(csv: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < csv.length; i++) {
    const ch = csv[i];
    if (ch === '"') {
      if (quoted && csv[i + 1] === '"') {
        field += '"';
        i++;
      } else {
        quoted = !quoted;
      }
    } else if (!quoted && ch === ",") {
      row.push(field);
      field = "";
    } else if (!quoted && (ch === "\n" || ch === "\r")) {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      if (ch === "\r" && csv[i + 1] === "\n") i++;
    } else {
      field += ch;
    }
  }
  if (row.length || field || (csv && !/[\r\n]$/.test(csv))) rows.push([...row, field]);
  return rows;
}

export function writeTableCsv(rows: string[][]): string {
  return rows.map((row) => row.map((cell) =>
    /[",\r\n]/.test(cell) ? '"' + cell.replaceAll('"', '""') + '"' : cell,
  ).join(",")).join("\r\n");
}
