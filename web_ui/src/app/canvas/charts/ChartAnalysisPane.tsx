/** Real data-space chart interactions. Kept separate from the legacy SVG
 * pane so bar/pie/heatmap/sankey retain their historic pan/zoom behavior.
 * Every committed annotation is expressed in original data units.
 */
import { useEffect, useId, useRef, useState, type PointerEvent, type RefObject, type KeyboardEvent, type WheelEvent } from "react";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import {
  canonicalizeChartAnalysis, chartAnalysisOf, usesScientificMarks,
  type ChartAnalysis, type ChartAnnotation,
} from "../chartSpec";
import type { ChartTheme } from "./chartTheme";
import { LineChartMarks } from "./LineChartMarks";
import { ScientificChartMarks } from "./ScientificChartMarks";
import { HistogramChartMarks } from "./HistogramChartMarks";
import { BoxplotChartMarks } from "./BoxplotChartMarks";
import { ChartTooltip, type TooltipContent } from "./ChartTooltip";
import {
  chartFrame, datumAt, domainFromPixels, panDomain, scaleFor, zoomDomain,
  type ViewDomains,
} from "./chartViewport";

type Mode = "pan" | "zoom" | "label" | "arrow" | "region-x" | "region-y";
type Pointer = { px: number; py: number; x: number; y: number };
interface Drag {
  id: number;
  mode: Mode;
  start: Pointer;
  last: Pointer;
  origin: ViewDomains;
}

export interface ChartAnalysisPaneProps {
  chartType: string;
  chartData: ChartDataRow;
  width: number;
  height: number;
  theme: ChartTheme;
  wrapperRef: RefObject<HTMLDivElement>;
  onAnalysisChange?: (analysis: ChartAnalysis) => void | Promise<void>;
}

export function ChartAnalysisPane({
  chartType, chartData, width, height, theme, wrapperRef, onAnalysisChange,
}: ChartAnalysisPaneProps) {
  const frame = chartFrame(chartData, chartType, width, height);
  const persisted = chartAnalysisOf(chartData);
  const [override, setOverride] = useState<ChartAnalysis | null>(null);
  const analysis = override ?? persisted;
  const [view, setView] = useState<ViewDomains | null>(null);
  const current: ViewDomains = {
    x: analysis.lockX && analysis.xRange ? analysis.xRange : view?.x ?? frame.base.x,
    y: analysis.lockY && analysis.yRange ? analysis.yRange : view?.y ?? frame.base.y,
  };
  const [mode, setMode] = useState<Mode>("pan");
  const [toolsOpen, setToolsOpen] = useState(false);
  const [labelText, setLabelText] = useState("");
  const [cursor, setCursor] = useState<Pointer | null>(null);
  const [selection, setSelection] = useState<{ a: Pointer; b: Pointer } | null>(null);
  const [hover, setHover] = useState<TooltipContent | null>(null);
  const [error, setError] = useState("");
  const drag = useRef<Drag | null>(null);
  const arrowId = useId();
  const clipId = useId();
  const toolsId = useId();

  // An undo/reload/remote scene update is authoritative over optimistic edits.
  // A data update does not reset an uncommitted, purely local zoom.
  useEffect(() => { setOverride(null); }, [chartData]);

  function commit(next: ChartAnalysis): void {
    try {
      const normalized = canonicalizeChartAnalysis(
        next, chartType, frame.xKind, frame.yKind,
      );
      setError("");
      setOverride(normalized);
      Promise.resolve(onAnalysisChange?.(normalized)).catch((reason: unknown) => {
        setOverride(null);
        setError(reason instanceof Error ? reason.message : "Could not save chart annotation");
        setToolsOpen(true);
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invalid annotation");
      setToolsOpen(true);
    }
  }

  function withAnnotation(annotation: ChartAnnotation): void {
    commit({ ...analysis, annotations: [...analysis.annotations, annotation] });
  }

  const inside = (px: number, py: number): boolean =>
    px >= frame.left && px <= frame.left + frame.width &&
    py >= frame.top && py <= frame.top + frame.height;
  const clampPoint = (px: number, py: number) => ({
    px: Math.max(frame.left, Math.min(frame.left + frame.width, px)),
    py: Math.max(frame.top, Math.min(frame.top + frame.height, py)),
  });
  function pointAt(px: number, py: number): Pointer {
    const point = datumAt(px, py, frame, current);
    return { px, py, ...point };
  }
  function eventPoint(event: PointerEvent<SVGSVGElement> | WheelEvent<SVGSVGElement>): Pointer {
    const rect = event.currentTarget.getBoundingClientRect();
    const p = clampPoint(event.clientX - rect.left, event.clientY - rect.top);
    return pointAt(p.px, p.py);
  }

  function onPointerDown(event: PointerEvent<SVGSVGElement>): void {
    if (event.button !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!inside(event.clientX - rect.left, event.clientY - rect.top)) return;
    const p = eventPoint(event);
    setCursor(p);
    if (mode === "label") {
      withAnnotation({ kind: "label", x: p.x, y: p.y, text: labelText });
      return;
    }
    drag.current = { id: event.pointerId, mode, start: p, last: p, origin: current };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.stopPropagation();
  }

  function onPointerMove(event: PointerEvent<SVGSVGElement>): void {
    const p = eventPoint(event);
    const rect = event.currentTarget.getBoundingClientRect();
    setCursor(inside(event.clientX - rect.left, event.clientY - rect.top) ? p : null);
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    active.last = p;
    if (active.mode === "pan") {
      setView({
        x: analysis.lockX ? active.origin.x : panDomain(
          active.origin.x, frame.xKind, -(p.px - active.start.px) / frame.width,
        ),
        y: analysis.lockY ? active.origin.y : panDomain(
          active.origin.y, frame.yKind, (p.py - active.start.py) / frame.height,
        ),
      });
    } else {
      setSelection({ a: active.start, b: p });
    }
  }

  function onPointerUp(event: PointerEvent<SVGSVGElement>): void {
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    drag.current = null;
    setSelection(null);
    if (active.mode === "pan") return;
    const a = active.start;
    const b = eventPoint(event);
    const dx = Math.abs(b.px - a.px);
    const dy = Math.abs(b.py - a.py);
    if (active.mode === "zoom") {
      if ((!analysis.lockX && dx < 5) || (!analysis.lockY && dy < 5)) return;
      setView({
        x: analysis.lockX ? current.x : domainFromPixels(
          a.px - frame.left, b.px - frame.left, active.origin.x, [0, frame.width], frame.xKind,
        ),
        y: analysis.lockY ? current.y : domainFromPixels(
          a.py - frame.top, b.py - frame.top, active.origin.y, [frame.height, 0], frame.yKind,
        ),
      });
    } else if (active.mode === "arrow" && Math.hypot(dx, dy) >= 5) {
      withAnnotation({ kind: "arrow", x: a.x, y: a.y, x2: b.x, y2: b.y });
    } else if (active.mode === "region-x" && dx >= 5) {
      withAnnotation({ kind: "region", axis: "x", from: Math.min(a.x, b.x), to: Math.max(a.x, b.x) });
    } else if (active.mode === "region-y" && dy >= 5) {
      withAnnotation({ kind: "region", axis: "y", from: Math.min(a.y, b.y), to: Math.max(a.y, b.y) });
    }
  }

  function onWheel(event: WheelEvent<SVGSVGElement>): void {
    event.preventDefault();
    const p = eventPoint(event);
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    setView({
      x: analysis.lockX ? current.x : zoomDomain(current.x, frame.xKind, p.x, factor),
      y: analysis.lockY ? current.y : zoomDomain(current.y, frame.yKind, p.y, factor),
    });
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>): void {
    const key = event.key;
    if (key === "0" || key === "Home") {
      event.preventDefault(); event.stopPropagation();
      setView(null);
      return;
    }
    if (key === "+" || key === "=" || key === "-" || key === "_") {
      event.preventDefault(); event.stopPropagation();
      const center = datumAt(frame.left + frame.width / 2, frame.top + frame.height / 2, frame, current);
      const factor = key === "+" || key === "=" ? 1.25 : 0.8;
      setView({
        x: analysis.lockX ? current.x : zoomDomain(current.x, frame.xKind, center.x, factor),
        y: analysis.lockY ? current.y : zoomDomain(current.y, frame.yKind, center.y, factor),
      });
      return;
    }
    const shifts: Record<string, [number, number]> = {
      ArrowLeft: [-0.1, 0], ArrowRight: [0.1, 0],
      ArrowUp: [0, 0.1], ArrowDown: [0, -0.1],
    };
    if (shifts[key]) {
      event.preventDefault(); event.stopPropagation();
      if (event.shiftKey) {
        const p = cursor ?? pointAt(frame.left + frame.width / 2, frame.top + frame.height / 2);
        const px = Math.max(frame.left, Math.min(frame.left + frame.width, p.px + shifts[key][0] * frame.width / 4));
        const py = Math.max(frame.top, Math.min(frame.top + frame.height, p.py - shifts[key][1] * frame.height / 4));
        setCursor(pointAt(px, py));
        return;
      }
      setView({
        x: analysis.lockX ? current.x : panDomain(current.x, frame.xKind, shifts[key][0]),
        y: analysis.lockY ? current.y : panDomain(current.y, frame.yKind, shifts[key][1]),
      });
    }
  }

  function toggleLock(axis: "x" | "y"): void {
    // Unlocking does not unexpectedly jump back to the data domain.
    setView(current);
    if (axis === "x") {
      commit({ ...analysis, lockX: !analysis.lockX, xRange: !analysis.lockX ? [current.x[0], current.x[1]] : undefined });
    } else {
      commit({ ...analysis, lockY: !analysis.lockY, yRange: !analysis.lockY ? [current.y[0], current.y[1]] : undefined });
    }
  }

  function applyPreciseRanges(form: HTMLFormElement): void {
    const fields = new FormData(form);
    try {
      const proposedX: [number, number] = [Number(fields.get("xmin")), Number(fields.get("xmax"))];
      const proposedY: [number, number] = [Number(fields.get("ymin")), Number(fields.get("ymax"))];
      const parsed = canonicalizeChartAnalysis({
        ...analysis, xRange: proposedX, yRange: proposedY,
      }, chartType, frame.xKind, frame.yKind);
      setView({ x: parsed.xRange!, y: parsed.yRange! });
      if (analysis.lockX || analysis.lockY) {
        commit({
          ...analysis,
          xRange: analysis.lockX ? parsed.xRange : analysis.xRange,
          yRange: analysis.lockY ? parsed.yRange : analysis.yRange,
        });
      } else setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invalid axis limits");
    }
  }

  function preciseAnnotation(form: HTMLFormElement): void {
    const values = new FormData(form);
    const n = (field: string) => Number(values.get(field));
    if (mode === "label") withAnnotation({ kind: "label", x: n("x"), y: n("y"), text: String(values.get("text") ?? "") });
    else if (mode === "arrow") withAnnotation({ kind: "arrow", x: n("x"), y: n("y"), x2: n("x2"), y2: n("y2") });
    else if (mode === "region-x" || mode === "region-y") {
      withAnnotation({ kind: "region", axis: mode === "region-x" ? "x" : "y", from: n("from"), to: n("to") });
    }
  }

  const showViewport = view !== null || analysis.lockX || analysis.lockY;
  const activeViewport = showViewport ? current : undefined;
  const x = scaleFor(current.x, [frame.left, frame.left + frame.width], frame.xKind);
  const y = scaleFor(current.y, [frame.top + frame.height, frame.top], frame.yKind);
  const center = cursor ?? pointAt(frame.left + frame.width / 2, frame.top + frame.height / 2);
  const second = pointAt(frame.left + frame.width * 0.7, frame.top + frame.height * 0.3);
  const exact = (value: number, unit?: string | null) => String(value) + (unit ? " " + unit : "");
  const annotationButtons: { mode: Mode; title: string }[] = [
    { mode: "pan", title: "Pan" }, { mode: "zoom", title: "Box zoom" },
    { mode: "label", title: "Text label" }, { mode: "arrow", title: "Arrow" },
    { mode: "region-x", title: "Shade X" }, { mode: "region-y", title: "Shade Y" },
  ];

  return (
    <>
      <svg className="chart-canvas-svg nodrag nowheel nopan"
        tabIndex={0} role="application"
        aria-label={"Interactive " + chartType + " chart. Arrow keys pan; Shift plus arrows move crosshair; plus and minus zoom; Home or zero resets. Chart tools provide keyboard annotation entry."}
        width={width} height={height}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => { drag.current = null; setSelection(null); }}
        onPointerLeave={() => { if (!drag.current) setCursor(null); }}
        onWheel={onWheel} onKeyDown={onKeyDown}
        style={{ cursor: mode === "pan" ? "grab" : "crosshair" }}
      >
        {chartType === "line" && (usesScientificMarks(chartData, chartType)
          ? <ScientificChartMarks chartType={chartType} chartData={chartData} theme={theme} width={width} height={height}
              onHover={setHover} wrapperRef={wrapperRef} viewport={activeViewport} />
          : <LineChartMarks chartData={chartData} theme={theme} width={width} height={height}
              onHover={setHover} wrapperRef={wrapperRef} viewport={activeViewport} />)}
        {chartType === "scatter" && <ScientificChartMarks chartType={chartType} chartData={chartData} theme={theme}
          width={width} height={height} onHover={setHover} wrapperRef={wrapperRef} viewport={activeViewport} />}
        {chartType === "histogram" && <HistogramChartMarks chartData={chartData} theme={theme}
          width={width} height={height} onHover={setHover} wrapperRef={wrapperRef} viewport={activeViewport} />}
        {chartType === "boxplot" && <BoxplotChartMarks chartData={chartData} theme={theme}
          width={width} height={height} onHover={setHover} wrapperRef={wrapperRef} viewport={activeViewport} />}
        <defs>
          <clipPath id={clipId}><rect x={frame.left} y={frame.top} width={frame.width} height={frame.height} /></clipPath>
          <marker id={arrowId} markerWidth={8} markerHeight={8} refX={7} refY={4} orient="auto">
            <path d="M 0 0 L 8 4 L 0 8 Z" fill={theme.text} />
          </marker>
        </defs>
        <g className="chart-analysis-annotations" clipPath={"url(#" + clipId + ")"} pointerEvents="none">
          {analysis.annotations.map((item, index) => {
            if (item.kind === "region") {
              const low = item.axis === "x" ? x(item.from) : y(item.to);
              const high = item.axis === "x" ? x(item.to) : y(item.from);
              return <rect key={index} className="chart-analysis-region"
                x={item.axis === "x" ? low : frame.left} y={item.axis === "y" ? low : frame.top}
                width={item.axis === "x" ? high - low : frame.width}
                height={item.axis === "y" ? high - low : frame.height}
                fill={theme.textMuted} fillOpacity={0.18} />;
            }
            if (item.kind === "arrow") {
              return <line key={index} className="chart-analysis-arrow"
                x1={x(item.x)} y1={y(item.y)} x2={x(item.x2)} y2={y(item.y2)}
                stroke={theme.text} strokeWidth={1.8} markerEnd={"url(#" + arrowId + ")"} />;
            }
            return <text key={index} className="chart-analysis-label" x={x(item.x)} y={y(item.y)}
              fill={theme.text} fontSize={12} fontWeight={600}>{item.text}</text>;
          })}
          {selection && mode !== "pan" && (
            <rect x={Math.min(selection.a.px, selection.b.px)}
              y={mode === "region-x" ? frame.top : Math.min(selection.a.py, selection.b.py)}
              width={mode === "region-y" ? frame.width : Math.abs(selection.a.px - selection.b.px)}
              height={mode === "region-x" ? frame.height : Math.abs(selection.a.py - selection.b.py)}
              stroke={theme.text} strokeDasharray="4 3" fill={theme.textMuted} fillOpacity={0.12} />
          )}
          {cursor && <>
            <line className="chart-analysis-crosshair" x1={cursor.px} x2={cursor.px}
              y1={frame.top} y2={frame.top + frame.height} stroke={theme.textMuted} strokeDasharray="3 3" />
            <line className="chart-analysis-crosshair" x1={frame.left} x2={frame.left + frame.width}
              y1={cursor.py} y2={cursor.py} stroke={theme.textMuted} strokeDasharray="3 3" />
          </>}
        </g>
      </svg>
      <div className="chart-analysis-status nodrag nowheel"
        style={{ position: "absolute", bottom: 2, left: 6, fontSize: 10,
          background: theme.tooltipBackground, color: theme.text, padding: "2px 4px", pointerEvents: "none" }}
        role="status" aria-live="polite">
        {cursor
          ? "X: " + exact(cursor.x, chartData.xUnit) + " · Y: " + exact(cursor.y, chartData.yUnit)
          : "Chart: focus and use arrows, +/−, Home"}
      </div>
      <div className="chart-analysis-controls nodrag nowheel nopan"
        style={{ position: "absolute", right: 6, top: 4, zIndex: 5,
          maxWidth: "calc(100% - 12px)", color: theme.text, fontSize: 11 }}>
        <button type="button" aria-expanded={toolsOpen} aria-controls={toolsId}
          onClick={() => setToolsOpen(!toolsOpen)}>Chart tools</button>
        {view && <button type="button" onClick={() => setView(null)}>Reset view</button>}
        {toolsOpen && (
          <div id={toolsId} role="group" aria-label="Chart analysis tools"
            style={{ padding: 6, background: theme.tooltipBackground, border: "1px solid " + theme.tooltipBorder,
              maxHeight: Math.max(120, height - 55), overflowY: "auto", maxWidth: 320 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {annotationButtons.map((item) => (
                <button type="button" key={item.mode}
                  aria-pressed={mode === item.mode}
                  onClick={() => { setMode(item.mode); setSelection(null); }}>
                  {item.title}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button type="button" aria-pressed={analysis.lockX} onClick={() => toggleLock("x")}>
                {analysis.lockX ? "Unlock X" : "Lock X range"}
              </button>
              <button type="button" aria-pressed={analysis.lockY} onClick={() => toggleLock("y")}>
                {analysis.lockY ? "Unlock Y" : "Lock Y range"}
              </button>
              <button type="button" onClick={() => setView(null)}>Reset zoom</button>
            </div>
            <form aria-label="Set exact visible axis ranges" onSubmit={(event) => {
              event.preventDefault(); applyPreciseRanges(event.currentTarget);
            }} style={{ marginTop: 6 }}>
              <label>X min <input aria-label="X minimum" name="xmin" type="number" step="any"
                required defaultValue={current.x[0]} style={{ width: 84 }} /></label>
              <label>X max <input aria-label="X maximum" name="xmax" type="number" step="any"
                required defaultValue={current.x[1]} style={{ width: 84 }} /></label>
              <label>Y min <input aria-label="Y minimum" name="ymin" type="number" step="any"
                required defaultValue={current.y[0]} style={{ width: 84 }} /></label>
              <label>Y max <input aria-label="Y maximum" name="ymax" type="number" step="any"
                required defaultValue={current.y[1]} style={{ width: 84 }} /></label>
              <button type="submit">Apply ranges</button>
            </form>
            {mode === "label" && <label>Label text{" "}
              <input aria-label="Annotation label" maxLength={160} value={labelText}
                onChange={(event) => setLabelText(event.target.value)} />
            </label>}
            {mode !== "pan" && mode !== "zoom" && (
              <form key={mode} aria-label="Precise annotation coordinates" onSubmit={(event) => {
                event.preventDefault(); preciseAnnotation(event.currentTarget);
              }} style={{ marginTop: 6 }}>
                {mode === "label" || mode === "arrow" ? <>
                  <label>X <input type="number" step="any" name="x" aria-label="Annotation X" required
                    defaultValue={center.x} style={{ width: 90 }} /></label>
                  <label>Y <input type="number" step="any" name="y" aria-label="Annotation Y" required
                    defaultValue={center.y} style={{ width: 90 }} /></label>
                  {mode === "label" && <label>Text <input name="text" aria-label="Annotation text" required
                    maxLength={160} defaultValue={labelText} /></label>}
                  {mode === "arrow" && <>
                    <label>To X <input type="number" step="any" name="x2" aria-label="Arrow end X" required
                      defaultValue={second.x} style={{ width: 90 }} /></label>
                    <label>To Y <input type="number" step="any" name="y2" aria-label="Arrow end Y" required
                      defaultValue={second.y} style={{ width: 90 }} /></label>
                  </>}
                </> : <>
                  <label>From <input type="number" step="any" name="from" required aria-label="Region start"
                    defaultValue={mode === "region-x" ? current.x[0] : current.y[0]} style={{ width: 90 }} /></label>
                  <label>To <input type="number" step="any" name="to" required aria-label="Region end"
                    defaultValue={mode === "region-x" ? current.x[1] : current.y[1]} style={{ width: 90 }} /></label>
                </>}
                <button type="submit">Add annotation</button>
              </form>
            )}
            {analysis.annotations.length > 0 && <button type="button" onClick={() => {
              commit({ ...analysis, annotations: analysis.annotations.slice(0, -1) });
            }}>Remove last annotation</button>}
            {error && <div role="alert">{error}</div>}
          </div>
        )}
      </div>
      <ChartTooltip content={hover} theme={theme} />
    </>
  );
}
