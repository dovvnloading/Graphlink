import type { CSSProperties } from "react";
import { formatScienceNumber } from "./scientificWorkbench";
import type { SciencePlateMapView, ScienceResultView, ScienceSurvivalCurveView } from "./scientificResultViews";
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
  const values = new Map(view.wells.map(({ well, value }) => [well, value]));
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
              const ratio = number ? maximum === minimum ? 0.5 : (value - minimum) / (maximum - minimum) : 0;
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
    <small>Hover or focus a well to read its full value; blank wells are not zero.</small>
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

export function ScientificResultViews({ views }: { views: ScienceResultView[] }) {
  return <div className="science-result-views" aria-label="Typed result views">
    {views.map((view, index) => view.type === "plate_map"
      ? <PlateMap key={index} view={view} /> : <SurvivalCurve key={index} view={view} />)}
  </div>;
}
