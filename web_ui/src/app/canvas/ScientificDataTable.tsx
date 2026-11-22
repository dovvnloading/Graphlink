import { useState } from "react";

/** A keyboard-readable alternative to SVG, bounded even at the largest view limits. */
export function ScientificDataTable({ title, columns, rows }: {
  title: string; columns: string[]; rows: () => string[][];
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const data = open ? rows() : [];
  const pageSize = 40;
  const visiblePage = Math.min(page, Math.max(0, Math.ceil(data.length / pageSize) - 1));
  const first = visiblePage * pageSize;
  return <details className="science-view-data" open={open}>
    <summary onClick={event => { event.preventDefault(); setOpen(!open); setPage(0); }}>{title}</summary>
    {open && <><div className="science-view-data-scroll"><table aria-label={title}>
      <thead><tr>{columns.map((column, index) => <th scope="col" key={index}>{column}</th>)}</tr></thead>
      <tbody>{data.slice(first, first + pageSize).map((row, index) => <tr key={index}>
        {row.map((value, col) => col === 0 ? <th scope="row" key={col}>{value}</th> : <td key={col}>{value}</td>)}
      </tr>)}</tbody></table></div>
      {data.length > pageSize && <div className="science-actions"><button type="button" disabled={!visiblePage}
        onClick={() => setPage(visiblePage - 1)}>Previous rows</button>
        <small role="status">Rows {first + 1}–{Math.min(first + pageSize, data.length)} of {data.length}</small>
        <button type="button" disabled={first + pageSize >= data.length} onClick={() => setPage(visiblePage + 1)}>Next rows</button></div>}
    </>}
  </details>;
}
