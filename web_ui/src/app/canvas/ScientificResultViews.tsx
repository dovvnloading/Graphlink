import { useId, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { ScientificDataTable } from "./ScientificDataTable";
import { exactScienceNumber, formatScienceNumber } from "./scientificWorkbench";
import type { ScienceForestPlotView, SciencePhylogeneticNode, SciencePhylogeneticTreeView, SciencePlateMapView, ScienceResultView, ScienceSurvivalCurveView, ScienceVolcanoPlotView, ScienceVolcanoPoint } from "./scientificViewTypes";
import "./ScientificResultViews.css";

const ROWS = "ABCDEFGHIJKLMNOP";
const COLORS = [
  "#227ba5", "#ce623a", "#5b8236", "#9e4c88",
  "#9a7926", "#387f81", "#795ac2", "#ad5360",
  "#576a98", "#8b6947", "#428568", "#9e637b",
];
const DASHES = ["", "6 3", "2 3", "9 2 2 2"];

function PlateMap({ view }: { view: SciencePlateMapView }) {
  const rowCount = view.format === 96 ? 8 : 16;
  const colCount = view.format === 96 ? 12 : 24;
  const [activeIndex, setActiveIndex] = useState(0);
  const tableRef = useRef<HTMLTableElement>(null);
  const hint = useId();
  function move(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    const column = index % colCount;
    switch (event.key) {
      case "ArrowRight": next += column < colCount - 1 ? 1 : 0; break;
      case "ArrowLeft": next -= column > 0 ? 1 : 0; break;
      case "ArrowDown": next = Math.min((rowCount - 1) * colCount + column, index + colCount); break;
      case "ArrowUp": next = Math.max(column, index - colCount); break;
      case "Home": next = event.ctrlKey ? 0 : index - column; break;
      case "End": next = event.ctrlKey ? view.format - 1 : index - column + colCount - 1; break;
      default: return;
    }
    event.preventDefault();
    event.stopPropagation();
    tableRef.current?.querySelector<HTMLButtonElement>('[data-well-index="' + next + '"]')?.focus();
  }
  const values = new Map<string, number | string | null>(
    view.wells.map(({ well, value }): [string, number | string | null] => [well, value]),
  );
  const numeric = view.wells.flatMap(({ value }) => typeof value === "number" ? [value] : []);
  const minimum = numeric.length ? Math.min(...numeric) : 0;
  const maximum = numeric.length ? Math.max(...numeric) : 0;
  const count = view.wells.filter((item) => item.value !== null).length;

  const selected = ROWS[Math.floor(activeIndex / colCount)] + (activeIndex % colCount + 1);
  function reading(well: string) {
    const value = values.get(well);
    return value == null ? "empty" : typeof value === "number"
      ? exactScienceNumber(value) + (view.unit ? " " + view.unit : "") : value;
  }
  return <figure className="science-typed-view science-plate-map">
    <figcaption>{view.title}</figcaption>
    <small>{view.format} wells · {count} readings · {view.format - count} empty</small>
    <div className="science-plate-scroll">
      <table ref={tableRef} aria-label={view.format + "-well plate map"} aria-describedby={hint}>
        <thead><tr><th scope="col">Row</th>{Array.from({ length: colCount }, (_, index) =>
          <th scope="col" key={index}>{index + 1}</th>)}</tr></thead>
        <tbody>{ROWS.slice(0, rowCount).split("").map((row, rowIndex) =>
          <tr key={row}><th scope="row">{row}</th>
            {Array.from({ length: colCount }, (_, index) => {
              const well = row + (index + 1), value = values.get(well);
              const empty = value === undefined || value === null;
              const number = typeof value === "number";
              const cellIndex = rowIndex * colCount + index;
              const description = well + ": " + reading(well);
              const range = maximum - minimum;
              const ratio = number ? maximum === minimum ? 0.5 :
                Number.isFinite(range) ? (value - minimum) / range :
                  (value / 2 - minimum / 2) / (maximum / 2 - minimum / 2) : 0;
              const fill: CSSProperties | undefined = number
                ? { backgroundColor: `hsl(209 78% ${87 - ratio * 47}%)`, color: ratio >= 0.57 ? "#fff" : "#14304e" }
                : undefined;
              return <td key={well} aria-label={description} title={description}
                className={empty ? "science-well-empty" : number ? "science-well-number" : "science-well-text"}>
                <button type="button" className="science-well-button" data-well-index={cellIndex}
                  tabIndex={activeIndex === cellIndex ? 0 : -1} aria-label={description}
                  onKeyDown={event => move(event, cellIndex)} onFocus={() => setActiveIndex(cellIndex)}
                  onClick={() => setActiveIndex(cellIndex)}>
                  <span style={fill}>{empty ? "·" : number ? "●" : String(value).slice(0, 2)}</span>
                </button>
              </td>;
            })}
          </tr>)}</tbody>
      </table>
    </div>
    {numeric.length > 0 && <small className="science-plate-key">Numeric scale: {formatScienceNumber(minimum)}
      {" – "}{formatScienceNumber(maximum)}{view.unit ? " " + view.unit : ""}. Colors encode the observed range.</small>}
    <p className="science-well-readout"><strong>{selected}</strong> · {reading(selected)}</p>
    <small id={hint}>Select a well or use the arrow keys to inspect its reading. Home and End move within a row.</small>
  </figure>;
}

function SurvivalCurve({ view }: { view: ScienceSurvivalCurveView }) {
  const latest = Math.max(0, ...view.groups.flatMap((group) => group.times));
  const x = (time: number) => 38 + 304 * (latest ? time / latest : 0);
  const y = (probability: number) => 164 - probability * 144;
  const path = (times: number[], probabilities: number[]) => times.slice(1).reduce(
    (d, time, index) => d + `H${x(time)}V${y(probabilities[index + 1])}`,
    `M${x(times[0])},${y(probabilities[0])}`,
  );

  return <figure className="science-typed-view science-survival">
    <figcaption>{view.title}</figcaption>
    <small>Kaplan–Meier survival probability · vertical ticks indicate censoring</small>
    <svg viewBox="0 0 355 201" role="img"
      aria-label={view.title + ": survival curves for " + view.groups.map((group) => group.label).join(", ")}>
      {[0, 0.25, 0.5, 0.75, 1].map((p) =>
        <g key={p}>
          <line x1="38" y1={y(p)} x2="342" y2={y(p)} stroke="currentColor" opacity=".15" />
          <text x="33" y={y(p) + 3} textAnchor="end" className="science-survival-axis">{p}</text>
        </g>)}
      <path d="M38 20V164H342" stroke="currentColor" strokeWidth="1" opacity=".5" fill="none" />
      {view.groups.map((group, index) => {
        const color = COLORS[index];
        return <g key={group.label} stroke={color} fill="none">
          <path d={path(group.times, group.survival)} strokeWidth="2.2"
            strokeDasharray={DASHES[index % DASHES.length]} strokeLinejoin="round">
            <title>{group.label} Kaplan–Meier survival curve</title>
          </path>
          {(group.censored ?? []).map((time) => {
            const position = group.times.lastIndexOf(time);
            return <line key={time} x1={x(time)} x2={x(time)}
              y1={y(group.survival[position]) - 4} y2={y(group.survival[position]) + 4}
              strokeWidth="2"><title>{group.label}: censored at {formatScienceNumber(time)}</title></line>;
          })}
        </g>;
      })}
      <text x="38" y="177" className="science-survival-axis">0</text>
      <text x="342" y="177" textAnchor="end" className="science-survival-axis">{formatScienceNumber(latest)}</text>
      <text x="190" y="194" textAnchor="middle" className="science-survival-axis">{view.xLabel}</text>
    </svg>
    <ul className="science-survival-legend">{view.groups.map((group, index) =>
      <li key={group.label}><svg className="science-survival-key" viewBox="0 0 32 8" aria-hidden="true">
        <line x1="0" y1="4" x2="32" y2="4" stroke={COLORS[index]} strokeWidth="2.2"
          strokeDasharray={DASHES[index % DASHES.length]} /></svg>
        {group.label} · {group.censored?.length ?? 0} censor times
      </li>)}</ul>
    <ScientificDataTable title="Survival curve data" columns={["Group", view.xLabel, "Survival probability", "Censor time"]}
      rows={() => view.groups.flatMap(group => {
        const censored = new Set(group.censored);
        return group.times.map((time, i) => [group.label, exactScienceNumber(time),
          exactScienceNumber(group.survival[i]), censored.has(time) && group.times.lastIndexOf(time) === i ? "Yes" : "—"]);
      })} />
  </figure>;
}


type VolcanoClass = "up" | "down" | "other";
function VolcanoMark({ kind, x, y, label }: { kind: VolcanoClass; x: number; y: number; label?: string }) {
  const css = "science-volcano-point science-volcano-" + kind;
  const title = label && <title>{label}</title>;
  if (kind === "other") return <circle className={css} cx={x} cy={y} r="3.1">{title}</circle>;
  const sign = kind === "up" ? 1 : -1;
  return <path className={css} d={`M${x},${y - sign * 4}l3.8,${sign * 7}h-7.6z`}>{title}</path>;
}

/** A volcano plot is a scatter: joining features into a polyline would imply ordering. */
function VolcanoPlot({ view }: { view: ScienceVolcanoPlotView }) {
  const points = view.points;
  const xExtent = Math.max(1, view.log2FoldChangeThreshold,
    ...points.map((point) => Math.abs(point.log2FoldChange)));
  // q=0 represents underflow, not an infinite SVG coordinate.
  const negLogQ = (q: number) => -Math.log10(Math.max(q, 1e-300));
  const yExtent = Math.max(1, negLogQ(view.fdrThreshold),
    ...points.map((point) => negLogQ(point.qValue))) * 1.08;
  const x = (fold: number) => 39 + (fold / xExtent + 1) * 156;
  const y = (q: number) => 187 - (negLogQ(q) / yExtent) * 160;
  const category = (point: ScienceVolcanoPoint): "up" | "down" | "other" => {
    if (point.qValue > view.fdrThreshold) return "other";
    if (point.log2FoldChange > 0 && point.log2FoldChange >= view.log2FoldChangeThreshold) return "up";
    if (point.log2FoldChange < 0 && point.log2FoldChange <= -view.log2FoldChangeThreshold) return "down";
    return "other";
  };
  const counts = {
    up: points.filter((point) => category(point) === "up").length,
    down: points.filter((point) => category(point) === "down").length,
    other: points.filter((point) => category(point) === "other").length,
  };
  const floorUsed = points.some((point) => point.qValue === 0);

  return <figure className="science-typed-view science-volcano">
    <figcaption>{view.title}</figcaption>
    <small>Welch tests · BH-adjusted q-values · {points.length} tested features</small>
    <svg viewBox="0 0 390 240" role="img" aria-label={
      `${view.title}: volcano scatter of ${points.length} genes, ${view.xLabel} versus negative log10 adjusted p-value.`
    }>
      <path d="M39 27V187H351" fill="none" stroke="currentColor" opacity=".55" />
      <line className="science-volcano-zero" x1={x(0)} x2={x(0)} y1="27" y2="187"
        stroke="currentColor" opacity=".25" />
      {[view.log2FoldChangeThreshold, -view.log2FoldChangeThreshold].map((threshold, i) =>
        <line className="science-volcano-fold-threshold" key={i} x1={x(threshold)} x2={x(threshold)}
          y1="27" y2="187" stroke="currentColor" opacity=".6" strokeDasharray="4 4" />)}
      <line className="science-volcano-fdr-threshold" x1="39" x2="351"
        y1={y(view.fdrThreshold)} y2={y(view.fdrThreshold)}
        stroke="currentColor" opacity=".6" strokeDasharray="4 4" />
      {points.map(point => <VolcanoMark key={point.gene} kind={category(point)} x={x(point.log2FoldChange)} y={y(point.qValue)}
        label={point.gene + ": log2FC " + exactScienceNumber(point.log2FoldChange) + ", BH q " + exactScienceNumber(point.qValue)} />)}
      <text x="34" y="190" textAnchor="end" className="science-volcano-axis">0</text>
      <text x="34" y="30" textAnchor="end" className="science-volcano-axis">{formatScienceNumber(yExtent)}</text>
      <text x="39" y="201" textAnchor="middle" className="science-volcano-axis">−{formatScienceNumber(xExtent)}</text>
      <text x={x(0)} y="201" textAnchor="middle" className="science-volcano-axis">0</text>
      <text x="351" y="201" textAnchor="middle" className="science-volcano-axis">{formatScienceNumber(xExtent)}</text>
      <text x="195" y="228" textAnchor="middle" className="science-volcano-axis">{view.xLabel}</text>
      <text x="43" y="18" className="science-volcano-axis">−log10(BH q)</text>
    </svg>
    <ul className="science-volcano-legend" aria-label="Volcano significance counts">
      {(["up", "down", "other"] as const).map(kind => <li key={kind}>
        <svg aria-hidden="true" className="science-volcano-key" viewBox="0 0 12 12"><VolcanoMark kind={kind} x={6} y={6} /></svg>
        {{ up: "Up in B", down: "Down in B", other: "Other tested" }[kind]}: {counts[kind]}
      </li>)}
    </ul>
    <small>Dashed lines: q ≤ {formatScienceNumber(view.fdrThreshold)} and |log2FC| ≥ {formatScienceNumber(view.log2FoldChangeThreshold)}. Untested genes are excluded.</small>
    <ScientificDataTable title="Volcano feature data" columns={["Feature", "Log2 fold change", "BH q", "Classification"]}
      rows={() => points.map(point => [point.gene, exactScienceNumber(point.log2FoldChange),
        exactScienceNumber(point.qValue), { up: "Up in B", down: "Down in B", other: "Other tested" }[category(point)]])} />
    {floorUsed && <small>Underflowed q=0 plotted at 1e−300 (capped at −log10(q)=300).</small>}
  </figure>;
}

const forestNumber = (value: number) => formatScienceNumber(value);

function ForestPlot({ view }: { view: ScienceForestPlotView }) {
  const studies = view.studies;
  const reference = view.reference ?? (view.scale === "log" ? 1 : 0);
  const raw = [reference, view.pooled.lower, view.pooled.upper,
    ...studies.flatMap((study) => [study.lower, study.upper])];
  const transform = (value: number) => view.scale === "log" ? Math.log(value) : value;
  const minimum = Math.min(...raw.map(transform)), maximum = Math.max(...raw.map(transform));
  const fraction = (value: number) => {
    const transformed = transform(value);
    const difference = maximum - minimum;
    const ratio = difference === 0 ? .5 : Number.isFinite(difference)
      ? (transformed - minimum) / difference
      : (transformed / 2 - minimum / 2) / (maximum / 2 - minimum / 2);
    return Number.isFinite(ratio) ? Math.max(0, Math.min(1, ratio)) : .5;
  };
  const x = (value: number) => 156 + fraction(value) * 270;
  const rowY = (index: number) => 36 + index * 29;
  const pooledY = rowY(studies.length) + 7;
  const axisY = pooledY + 25;
  const height = axisY + 52;
  const maxWeight = Math.max(1, ...studies.map((study) => study.weight ?? 1));
  const ticks = minimum === maximum ? [minimum] :
    [0, .5, 1].map((part) => minimum * (1 - part) + maximum * part);
  const fromScale = (value: number) => view.scale === "log" ? Math.exp(value) : value;
  const pooled = view.pooled;
  const pooledLabel = pooled.label ?? "Pooled";

  return <figure className="science-typed-view science-forest">
    <figcaption>{view.title}</figcaption>
    <small>Study estimates with confidence intervals · diamond: pooled estimate · dashed line: reference ({forestNumber(reference)})</small>
    <div className="science-forest-scroll">
      <svg viewBox={"0 0 550 " + height} role="img"
        aria-label={view.title + ": forest plot with " + studies.length + " studies and pooled estimate on a " + view.scale + " axis"}>
        {studies.map((study, index) => <line key={"row-" + index}
          x1="156" x2="426" y1={rowY(index)} y2={rowY(index)}
          stroke="currentColor" opacity=".09" />)}
        <line className="science-forest-reference" x1={x(reference)} x2={x(reference)}
          y1="17" y2={axisY} stroke="currentColor" opacity=".6" strokeDasharray="4 4" />
        {studies.map((study, index) => {
          const middle = rowY(index), side = 3.5 + 6 * Math.sqrt((study.weight ?? 1) / maxWeight);
          const description = study.label + ": " + forestNumber(study.estimate) +
            " [" + forestNumber(study.lower) + ", " + forestNumber(study.upper) + "]";
          return <g className="science-forest-study" key={study.label}>
            <text x="8" y={middle + 4} className="science-forest-label">
              <title>{study.label}</title>{study.label.slice(0, 24)}
            </text>
            <line x1={x(study.lower)} x2={x(study.upper)} y1={middle} y2={middle}
              stroke="var(--gl-semantic-status-info, currentColor)" strokeWidth="2"><title>{description}</title></line>
            {[study.lower, study.upper].map((edge, i) =>
              <line key={i} x1={x(edge)} x2={x(edge)} y1={middle - 4} y2={middle + 4}
                stroke="var(--gl-semantic-status-info, currentColor)" strokeWidth="1.4" />)}
            <rect x={x(study.estimate) - side / 2} y={middle - side / 2} width={side} height={side}
              fill="var(--gl-semantic-status-info, currentColor)"><title>{description}</title></rect>
          </g>;
        })}
        <line x1="8" x2="426" y1={pooledY - 14} y2={pooledY - 14}
          stroke="currentColor" opacity=".25" />
        <text x="8" y={pooledY + 4} className="science-forest-label science-forest-pooled">{pooledLabel.slice(0, 24)}</text>
        <polygon className="science-forest-diamond"
          points={[x(pooled.lower) + "," + pooledY, x(pooled.estimate) + "," + (pooledY - 9),
            x(pooled.upper) + "," + pooledY, x(pooled.estimate) + "," + (pooledY + 9)].join(" ")}
          fill="var(--gl-semantic-status-info, currentColor)" stroke="currentColor" strokeWidth="1">
          <title>{pooledLabel + ": " + forestNumber(pooled.estimate) + " [" +
            forestNumber(pooled.lower) + ", " + forestNumber(pooled.upper) + "]"}</title>
        </polygon>
        <line x1="156" x2="426" y1={axisY} y2={axisY} stroke="currentColor" opacity=".65" />
        {ticks.map((tick, index) => {
          const value = fromScale(tick), position = x(value);
          return <g key={index}>
            <line x1={position} x2={position} y1={axisY} y2={axisY + 4} stroke="currentColor" />
            <text x={position} y={axisY + 17} textAnchor="middle"
              className="science-forest-axis">{forestNumber(value)}</text>
          </g>;
        })}
        <text x="291" y={height - 8} textAnchor="middle" className="science-forest-axis">
          {view.xLabel + " (" + view.scale + ")"}
        </text>
      </svg>
    </div>
    <details className="science-view-data">
      <summary>Exact estimates and confidence intervals</summary>
      <div className="science-view-data-scroll">
        <table aria-label={view.title + " estimate data"}>
          <thead><tr><th scope="col">Study</th><th scope="col">Estimate</th><th scope="col">Lower</th><th scope="col">Upper</th><th scope="col">Weight</th></tr></thead>
          <tbody>
            {studies.map((study) => <tr key={study.label}><th scope="row">{study.label}</th>
              <td>{exactScienceNumber(study.estimate)}</td><td>{exactScienceNumber(study.lower)}</td>
              <td>{exactScienceNumber(study.upper)}</td><td>{study.weight === undefined ? "—" : exactScienceNumber(study.weight)}</td>
            </tr>)}
            <tr><th scope="row">{pooledLabel}</th><td>{exactScienceNumber(pooled.estimate)}</td>
              <td>{exactScienceNumber(pooled.lower)}</td><td>{exactScienceNumber(pooled.upper)}</td><td>—</td></tr>
          </tbody>
        </table>
      </div>
    </details>
  </figure>;
}

interface PositionedTreeNode {
  id: number;
  data: SciencePhylogeneticNode;
  children: PositionedTreeNode[];
  depth: number;
  distance: number;
  tip: number;
}

function PhylogeneticTree({ view }: { view: SciencePhylogeneticTreeView }) {
  const all: PositionedTreeNode[] = [], tips: PositionedTreeNode[] = [];
  const build = (data: SciencePhylogeneticNode, depth: number, distance: number): PositionedTreeNode => {
    const node: PositionedTreeNode = { id: all.length, data, children: [], depth, distance, tip: 0 };
    all.push(node);
    if (data.children?.length) {
      node.children = data.children.map((child) => build(child, depth + 1, distance + (child.length ?? 0)));
      node.tip = (node.children[0].tip + node.children[node.children.length - 1].tip) / 2;
    } else {
      node.tip = tips.length;
      tips.push(node);
    }
    return node;
  };
  const root = build(view.tree, 0, 0);
  const maxDepth = Math.max(1, ...all.map((node) => node.depth));
  const maxDistance = Math.max(0, ...tips.map((node) => node.distance));
  const fraction = (node: PositionedTreeNode) => view.mode === "cladogram"
    ? (node.children.length ? node.depth / maxDepth : 1)
    : (maxDistance ? node.distance / maxDistance : 0);
  const name = (node: PositionedTreeNode) => node.data.name ?? ("Clade " + node.id);
  const branchTitle = (node: PositionedTreeNode) => name(node) +
    (node.data.length === undefined ? "" : " · length " + forestNumber(node.data.length)) +
    (node.data.support === undefined ? "" : " · support " + forestNumber(node.data.support));

  const rectangular = () => {
    const height = tips.length * 29 + 50;
    const x = (node: PositionedTreeNode) => 32 + fraction(node) * 342;
    const y = (node: PositionedTreeNode) => 26 + node.tip * 29;
    return <svg viewBox={"0 0 550 " + height} role="img"
      aria-label={view.title + ": rectangular " + view.mode + " of " + tips.length + " taxa"}>
      {all.filter((node) => node.children.length > 0).map((parent) =>
        <line key={"stem-" + parent.id} className="science-tree-branch"
          x1={x(parent)} x2={x(parent)} y1={y(parent.children[0])}
          y2={y(parent.children[parent.children.length - 1])} />)}
      {all.filter((node) => node !== root).map((node) => {
        const parent = all.find((item) => item.children.includes(node))!;
        const middle = (x(parent) + x(node)) / 2;
        return <g key={node.id}>
          <line className="science-tree-branch" x1={x(parent)} x2={x(node)}
            y1={y(node)} y2={y(node)}><title>{branchTitle(node)}</title></line>
          {x(node) - x(parent) > 33 && tips.length <= 24 && <>
            {node.data.support !== undefined && <text className="science-tree-annotation"
              x={middle} y={y(node) - 5} textAnchor="middle">{"S " + forestNumber(node.data.support)}</text>}
            {node.data.length !== undefined && <text className="science-tree-annotation"
              x={middle} y={y(node) + 12} textAnchor="middle">{forestNumber(node.data.length)}</text>}
          </>}
        </g>;
      })}
      {tips.map((tip) => <text key={tip.id} className="science-tree-tip"
        x={x(tip) + 7} y={y(tip) + 4}>
        <title>{name(tip)}</title>{name(tip).slice(0, 24)}
      </text>)}
    </svg>;
  };

  const circular = () => {
    const cx = 270, cy = 270, radius = 150;
    const angle = (node: PositionedTreeNode) => -Math.PI / 2 + Math.PI * 2 * (node.tip + .5) / tips.length;
    const r = (node: PositionedTreeNode) => fraction(node) * radius;
    const polar = (distance: number, theta: number): [number, number] =>
      [cx + distance * Math.cos(theta), cy + distance * Math.sin(theta)];
    const arc = (parent: PositionedTreeNode) => {
      if (r(parent) === 0 || parent.children.length < 2) return "";
      const first = angle(parent.children[0]), last = angle(parent.children[parent.children.length - 1]);
      const start = polar(r(parent), first), end = polar(r(parent), last);
      return "M" + start.join(",") + " A" + r(parent) + "," + r(parent) + " 0 " +
        (last - first > Math.PI ? 1 : 0) + " 1 " + end.join(",");
    };
    return <svg viewBox="0 0 540 540" role="img"
      aria-label={view.title + ": circular " + view.mode + " of " + tips.length + " taxa"}>
      {all.filter((node) => node.children.length > 1).map((node) =>
        <path key={"arc-" + node.id} className="science-tree-branch" d={arc(node)} />)}
      {all.filter((node) => node !== root).map((node) => {
        const parent = all.find((item) => item.children.includes(node))!;
        const start = polar(r(parent), angle(node)), end = polar(r(node), angle(node));
        const mid = polar((r(parent) + r(node)) / 2, angle(node));
        return <g key={node.id}>
          <line className="science-tree-branch" x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]}>
            <title>{branchTitle(node)}</title>
          </line>
          {tips.length <= 12 && r(node) - r(parent) > 18 && <>
            {node.data.support !== undefined && <text className="science-tree-annotation"
              x={mid[0]} y={mid[1] - 5} textAnchor="middle">{"S " + forestNumber(node.data.support)}</text>}
            {node.data.length !== undefined && <text className="science-tree-annotation"
              x={mid[0]} y={mid[1] + 10} textAnchor="middle">{forestNumber(node.data.length)}</text>}
          </>}
        </g>;
      })}
      {tips.map((node) => {
        const theta = angle(node), [tx, ty] = polar(r(node) + 9, theta);
        const left = Math.cos(theta) < -.1;
        return <text key={node.id} x={tx} y={ty + 4}
          textAnchor={left ? "end" : "start"} className="science-tree-tip">
          <title>{name(node)}</title>{name(node).slice(0, 19)}
        </text>;
      })}
    </svg>;
  };

  return <figure className="science-typed-view science-phylogeny">
    <figcaption>{view.title}</figcaption>
    <small>{view.layout === "rectangular" ? "Rectangular" : "Circular"} {view.mode} · {tips.length} tips ·
      {view.mode === "phylogram" ? " branch lengths determine distance from root (maximum " +
        forestNumber(maxDistance) + ")" : " equal-depth branching (lengths do not determine geometry)"}
      {" · S indicates branch support"}
    </small>
    <div className="science-phylogeny-scroll">
      {view.layout === "rectangular" ? rectangular() : circular()}
    </div>
    <details className="science-view-data">
      <summary>Branch lengths and support labels ({all.length - 1} branches)</summary>
      <div className="science-view-data-scroll">
        <table aria-label={view.title + " branch data"}>
          <thead><tr><th scope="col">Node/taxon</th><th scope="col">Length</th><th scope="col">Support</th></tr></thead>
          <tbody>{all.filter((node) => node !== root).map((node) =>
            <tr key={node.id}><th scope="row">{name(node)}</th>
              <td>{node.data.length === undefined ? "—" : exactScienceNumber(node.data.length)}</td>
              <td>{node.data.support === undefined ? "—" : exactScienceNumber(node.data.support)}</td>
            </tr>)}</tbody>
        </table>
      </div>
    </details>
  </figure>;
}

export function ScientificResultViews({ views }: { views: ScienceResultView[] }) {
  return <div className="science-result-views" aria-label="Typed result views">
    {views.map((view, index) => {
      switch (view.type) {
        case "plate_map": return <PlateMap key={index} view={view} />;
        case "survival_curve": return <SurvivalCurve key={index} view={view} />;
        case "forest_plot": return <ForestPlot key={index} view={view} />;
        case "volcano_plot": return <VolcanoPlot key={index} view={view} />;
        case "phylogenetic_tree": return <PhylogeneticTree key={index} view={view} />;
      }
    })}
  </div>;
}
