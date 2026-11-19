import type { CSSProperties } from "react";
import { formatScienceNumber } from "./scientificWorkbench";
import type { ScienceForestPlotView, SciencePhylogeneticNode, SciencePhylogeneticTreeView, SciencePlateMapView, ScienceResultView, ScienceSurvivalCurveView } from "./scientificViewTypes";
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
  const values = new Map<string, number | string | null>(
    view.wells.map(({ well, value }): [string, number | string | null] => [well, value]),
  );
  const numeric = view.wells.flatMap(({ value }) => typeof value === "number" ? [value] : []);
  const minimum = numeric.length ? Math.min(...numeric) : 0;
  const maximum = numeric.length ? Math.max(...numeric) : 0;
  const count = view.wells.filter((item) => item.value !== null).length;

  return <figure className="science-typed-view science-plate-map">
    <figcaption>{view.title}</figcaption>
    <small>{view.format} wells · {count} readings · {view.format - count} empty</small>
    <div className="science-plate-scroll">
      <table aria-label={view.format + "-well plate map"}>
        <thead><tr><th scope="col">Row</th>{Array.from({ length: colCount }, (_, index) =>
          <th scope="col" key={index}>{index + 1}</th>)}</tr></thead>
        <tbody>{ROWS.slice(0, rowCount).split("").map((row) =>
          <tr key={row}><th scope="row">{row}</th>
            {Array.from({ length: colCount }, (_, index) => {
              const well = row + (index + 1), value = values.get(well);
              const empty = value === undefined || value === null;
              const number = typeof value === "number";
              const label = empty ? "empty" : number ? formatScienceNumber(value) : value;
              const description = well + ": " + label + (number && view.unit ? " " + view.unit : "");
              const range = maximum - minimum;
              const ratio = number ? maximum === minimum ? 0.5 :
                Number.isFinite(range) ? (value - minimum) / range :
                  (value / 2 - minimum / 2) / (maximum / 2 - minimum / 2) : 0;
              const fill: CSSProperties | undefined = number
                ? { backgroundColor: `hsl(209 78% ${87 - ratio * 47}%)`, color: ratio >= 0.57 ? "#fff" : "#14304e" }
                : undefined;
              return <td key={well} aria-label={description} title={description}
                className={empty ? "science-well-empty" : number ? "science-well-number" : "science-well-text"}>
                <span style={fill}>{empty ? "·" : number ? "●" : String(value).slice(0, 2)}</span>
              </td>;
            })}
          </tr>)}</tbody>
      </table>
    </div>
    {numeric.length > 0 && <small className="science-plate-key">Numeric scale: {formatScienceNumber(minimum)}
      {" – "}{formatScienceNumber(maximum)}{view.unit ? " " + view.unit : ""}. Colors encode the observed range.</small>}
    <small>Hover a well for its full value; screen readers announce the well readings. Blank wells are not zero.</small>
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
      <li key={group.label}><span className="science-survival-line"
        style={{ borderColor: COLORS[index], borderTopStyle: index % 2 ? "dashed" : "solid" }} />
        {group.label} · {group.censored?.length ?? 0} censor times
      </li>)}</ul>
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
              stroke="var(--gl-status-info, currentColor)" strokeWidth="2"><title>{description}</title></line>
            {[study.lower, study.upper].map((edge, i) =>
              <line key={i} x1={x(edge)} x2={x(edge)} y1={middle - 4} y2={middle + 4}
                stroke="var(--gl-status-info, currentColor)" strokeWidth="1.4" />)}
            <rect x={x(study.estimate) - side / 2} y={middle - side / 2} width={side} height={side}
              fill="var(--gl-status-info, currentColor)"><title>{description}</title></rect>
          </g>;
        })}
        <line x1="8" x2="426" y1={pooledY - 14} y2={pooledY - 14}
          stroke="currentColor" opacity=".25" />
        <text x="8" y={pooledY + 4} className="science-forest-label science-forest-pooled">{pooledLabel.slice(0, 24)}</text>
        <polygon className="science-forest-diamond"
          points={[x(pooled.lower) + "," + pooledY, x(pooled.estimate) + "," + (pooledY - 9),
            x(pooled.upper) + "," + pooledY, x(pooled.estimate) + "," + (pooledY + 9)].join(" ")}
          fill="var(--gl-status-info, currentColor)" stroke="currentColor" strokeWidth="1">
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
              <td>{forestNumber(study.estimate)}</td><td>{forestNumber(study.lower)}</td>
              <td>{forestNumber(study.upper)}</td><td>{study.weight === undefined ? "—" : forestNumber(study.weight)}</td>
            </tr>)}
            <tr><th scope="row">{pooledLabel}</th><td>{forestNumber(pooled.estimate)}</td>
              <td>{forestNumber(pooled.lower)}</td><td>{forestNumber(pooled.upper)}</td><td>—</td></tr>
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
              <td>{node.data.length === undefined ? "—" : forestNumber(node.data.length)}</td>
              <td>{node.data.support === undefined ? "—" : forestNumber(node.data.support)}</td>
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
        case "phylogenetic_tree": return <PhylogeneticTree key={index} view={view} />;
      }
    })}
  </div>;
}
