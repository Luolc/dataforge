// One time-series chart, drawn by uPlot; every chart of the page shares the
// cursor and the time axis, and a drag across one zooms the page's window.
import "./chart.css";

import { useEffect, useRef, useState } from "react";
import uPlot from "uplot";

import { axisTime, DAY_S, moment } from "../../ui/format";
import { colour, cssVar, px, useTemplate } from "./theme";
import type { Pause } from "./types";

// Each colour a chart or a legend draws, by its token; the classes name
// every token, so that Tailwind keeps it in the built CSS.
const STROKES = {
  "series-1": "stroke-series-1",
  "series-2": "stroke-series-2",
  "series-3": "stroke-series-3",
  bar: "stroke-bar",
  bad: "stroke-bad",
  fg: "stroke-fg",
} as const;
type Colour = keyof typeof STROKES;

export interface Series {
  label: string;
  values: (number | null)[];
  color: Colour;
  dashed?: boolean;
  /**
   * What a bucket's value is, and so what the window's is: the mean of the
   * buckets', each an equal span of time, or the peak of them.
   */
  whole: "mean" | "peak";
  /** How its values read in the caption; the chart's own when not given. */
  format?: (value: number) => string;
}

/** The window's value of a series, from its buckets'; null with none. */
function overWindow(s: Series): number | null {
  const found = s.values.filter((v) => v !== null);
  if (found.length === 0) return null;
  return s.whole === "peak"
    ? Math.max(...found)
    : found.reduce((a, b) => a + b, 0) / found.length;
}

interface Props {
  title: string;
  times: number[];
  series: Series[];
  from: number;
  to: number;
  /** The y axis reaches at least this far, as the machine's CPUs. */
  max?: number;
  format: (value: number) => string;
  pauses: Pause[];
  onZoom: (from: number, to: number) => void;
}

export function Chart(props: Props) {
  const { title, times, series, from, to, max, format } = props;
  const boxRef = useRef<HTMLDivElement>(null);
  const template = useTemplate();
  const [cursor, setCursor] = useState<number | null>(null);
  // The latest props, read by uPlot's hooks without making a new chart on
  // every render.
  const propsRef = useRef(props);
  useEffect(() => {
    propsRef.current = props;
  });

  useEffect(() => {
    const el = boxRef.current;
    if (el === null) return;
    const height = px("--spacing-chart");
    const width = px("--stroke-taken");
    const dash = cssVar("--dash-not-taken").split(/\s+/).map(Number);
    const axis = {
      stroke: colour("fg-3"),
      grid: { stroke: colour("line-subtle"), width: 1 },
      ticks: { stroke: colour("line-subtle"), width: 1 },
      font: `${cssVar("--text-2xs")} ${getComputedStyle(el).fontFamily}`,
    };
    const options: uPlot.Options = {
      width: el.clientWidth,
      height,
      padding: [px("--spacing-chart-top"), px("--spacing-chart-pad"), 0, 0],
      legend: { show: false },
      cursor: {
        sync: { key: "monitor" },
        drag: { x: true, y: false, setScale: false },
        points: { show: false },
      },
      scales: {
        x: { time: true, range: [from, to] },
        y: {
          range: (_u, _min, dataMax) => [
            0,
            Math.max(max ?? 0, dataMax * 1.05, 1),
          ],
        },
      },
      axes: [
        {
          ...axis,
          // A dated tick is wider, so a longer window has fewer of them.
          ...(to - from > DAY_S && { space: px("--spacing-chart-day") }),
          values: (_u, splits) => splits.map((s) => axisTime(s, to - from)),
        },
        {
          ...axis,
          size: px("--spacing-chart-axis"),
          values: (_u, splits) => splits.map((s) => propsRef.current.format(s)),
        },
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: colour(s.color),
          width,
          ...(s.dashed === true && { dash }),
        })),
      ],
      hooks: {
        drawClear: [
          (u) => {
            // The pauses, as bands under the curves.
            const { ctx } = u;
            const { top, height: h } = u.bbox;
            ctx.save();
            ctx.fillStyle = colour("warn-bg");
            for (const p of propsRef.current.pauses) {
              const x0 = u.valToPos(Math.max(p.start_t, from), "x", true);
              const x1 = u.valToPos(Math.min(p.end_t ?? to, to), "x", true);
              ctx.fillRect(x0, top, Math.max(1, x1 - x0), h);
            }
            ctx.restore();
          },
        ],
        setSelect: [
          (u) => {
            const { left, width: w } = u.select;
            if (w < 4) return;
            const a = u.posToVal(left, "x");
            const b = u.posToVal(left + w, "x");
            u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
            propsRef.current.onZoom(a, b);
          },
        ],
        setCursor: [
          (u) => {
            setCursor(u.cursor.idx ?? null);
          },
        ],
      },
    };
    const data: uPlot.AlignedData = [times, ...series.map((s) => s.values)];
    const chart = new uPlot(options, data, el);
    const resize = new ResizeObserver(() => {
      chart.setSize({ width: el.clientWidth, height });
    });
    resize.observe(el);
    return () => {
      resize.disconnect();
      chart.destroy();
    };
  }, [times, series, from, to, max, template]);

  const at = cursor ?? times.length - 1;
  const t = times[at];
  const cell = "px-2 py-0.5 text-right whitespace-nowrap tabular-nums";
  return (
    <figure className="min-w-0">
      <figcaption className="flex flex-col gap-1 text-xs">
        <span className="font-semibold text-fg">{title}</span>
        <table aria-label={`${title}, values`} className="self-start">
          <thead className="text-fg-3">
            <tr>
              <th scope="col" className="sr-only">
                Series
              </th>
              <th scope="col" className={`${cell} font-normal`}>
                {t === undefined
                  ? "No sample"
                  : `${cursor === null ? "Last bucket" : "Bucket"}, from ${moment(t)}`}
              </th>
              <th scope="col" className={`${cell} font-normal`}>
                Over the window
              </th>
            </tr>
          </thead>
          <tbody>
            {series.map((s) => {
              const v = s.values[at];
              const whole = overWindow(s);
              const shown = s.format ?? format;
              return (
                <tr key={s.label}>
                  <th
                    scope="row"
                    className="pr-2 text-left font-normal whitespace-nowrap text-fg-3"
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <Swatch color={s.color} dashed={s.dashed === true} />
                      {s.label}
                    </span>
                  </th>
                  <td className={`${cell} text-fg`}>
                    {v === null || v === undefined ? "—" : shown(v)}
                  </td>
                  <td className={`${cell} text-fg`}>
                    {whole === null ? "—" : `${shown(whole)} ${s.whole}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </figcaption>
      <div ref={boxRef} data-monitor-chart className="mt-2" />
    </figure>
  );
}

/** A series' line in a legend. */
export function Swatch({ color, dashed }: { color: Colour; dashed: boolean }) {
  return (
    <svg viewBox="0 0 16 8" aria-hidden className="h-2 w-4 flex-none">
      <line
        x1="0"
        y1="4"
        x2="16"
        y2="4"
        className={STROKES[color]}
        strokeWidth="var(--stroke-taken)"
        strokeDasharray={dashed ? "var(--dash-not-taken)" : undefined}
      />
    </svg>
  );
}
