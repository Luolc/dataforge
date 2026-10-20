// The containers' time bars, one group per attempt, on the charts' time
// axis: a container's bar runs from its start to its end, or to its last
// sample when it has none.
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { useId, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import type { Project } from "../../api/types";
import { duration, instant } from "../../ui/format";
import { px } from "./theme";
import {
  barLabel,
  ending,
  failed,
  groupBars,
  type Group,
  lanes,
} from "./containers";
import type { Bar, Selected } from "./types";

export function Bars({
  project,
  bars,
  total,
  until,
  from,
  to,
  selected,
  onSelect,
}: {
  project: Project;
  bars: Bar[];
  total: number;
  /** When the first bar the limit left out starts; null when none is. */
  until: number | null;
  from: number;
  to: number;
  selected: Selected | null;
  onSelect: (s: Selected) => void;
}) {
  const groups = groupBars(bars);
  if (groups.length === 0) {
    return <p className="text-fg-3">No container ran in this window.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {until !== null && (
        <p className="text-xs text-fg-3">
          {bars.length.toLocaleString("en-US")} of{" "}
          {total.toLocaleString("en-US")} containers shown, those started before{" "}
          {instant(until)}; zoom in to see the rest.
        </p>
      )}
      {groups.map((g) => (
        <GroupRow
          key={g.key}
          project={project}
          group={g}
          from={from}
          to={to}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

const control =
  "target inline-flex items-center gap-1.5 rounded-row px-1.5 text-xs hover:bg-hover";

function GroupRow({
  project,
  group,
  from,
  to,
  selected,
  onSelect,
}: {
  project: Project;
  group: Group;
  from: number;
  to: number;
  selected: Selected | null;
  onSelect: (s: Selected) => void;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const bad = group.bars.filter(failed).length;
  const attemptOn =
    selected?.kind === "attempt" && selected.id === group.attempt;
  return (
    <section>
      <div className="flex flex-wrap items-center gap-x-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => {
            setOpen(!open);
          }}
          className={`${control} font-mono font-medium text-fg`}
        >
          {open ? (
            <ChevronDown className="size-icon-sm text-icon" aria-hidden />
          ) : (
            <ChevronRight className="size-icon-sm text-icon" aria-hidden />
          )}
          {group.label}
        </button>
        <span className="text-xs text-fg-3">
          {group.bars.length} containers
          {bad > 0 && (
            <>
              {", "}
              <span className="text-bad">{bad} exited non-zero</span>
            </>
          )}
        </span>
        {group.attempt !== null && (
          <>
            <button
              type="button"
              aria-pressed={attemptOn}
              onClick={() => {
                if (group.attempt !== null)
                  onSelect({ kind: "attempt", id: group.attempt });
              }}
              className={`${control} ${attemptOn ? "bg-active text-accent" : "text-fg-2"}`}
            >
              Curve
            </button>
            <Link
              to={`/${encodeURIComponent(project.id)}/attempt?${new URLSearchParams({ id: group.attempt }).toString()}`}
              className={`${control} link hover:underline`}
            >
              Attempt
              <ExternalLink className="size-icon-sm" aria-hidden />
            </Link>
          </>
        )}
      </div>
      <Lanes
        project={project}
        bars={group.bars}
        from={from}
        to={to}
        selected={selected}
        onSelect={onSelect}
      />
      <div id={listId} hidden={!open}>
        {open && (
          <ContainerList
            project={project}
            bars={group.bars}
            selected={selected}
            onSelect={onSelect}
          />
        )}
      </div>
    </section>
  );
}

function Lanes({
  project,
  bars,
  from,
  to,
  selected,
  onSelect,
}: {
  project: Project;
  bars: Bar[];
  from: number;
  to: number;
  selected: Selected | null;
  onSelect: (s: Selected) => void;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const lane = Math.max(1, px("--spacing-lane"));
  const [hover, setHover] = useState<{ bar: Bar; x: number; y: number }>();
  useEffect(() => {
    const el = boxRef.current;
    if (el === null) return;
    // It reports the first size on its own once it observes.
    const resize = new ResizeObserver(() => {
      setWidth(el.clientWidth);
    });
    resize.observe(el);
    return () => {
      resize.disconnect();
    };
  }, []);
  const assigned = lanes(bars);
  const count = Math.max(1, ...assigned.map((l) => l + 1));
  const x = (t: number) => ((t - from) / (to - from)) * width;
  // A bar narrower than a pixel or two is still drawn, and found within a
  // few pixels of the pointer.
  const least = px("--spacing-bar-least");
  const reach = px("--spacing-bar-reach");
  const at = (left: number, py: number) => {
    const row = Math.floor(py / lane);
    let best: Bar | undefined;
    let gap = Infinity;
    bars.forEach((bar, i) => {
      if (assigned[i] !== row) return;
      const x0 = x(bar.start_t);
      const x1 = Math.max(x(bar.last_t), x0 + least);
      const d = left < x0 ? x0 - left : left > x1 ? left - x1 : 0;
      if (d < gap && d <= reach) {
        gap = d;
        best = bar;
      }
    });
    return best;
  };
  return (
    <div className="relative mt-1 mr-chart-pad ml-chart-axis">
      {/* The pointer's shortcut to a container's curve; the list under the
          group's heading gives the keyboard the same. */}
      {/* eslint-disable-next-line jsx-a11y-x/click-events-have-key-events, jsx-a11y-x/no-static-element-interactions */}
      <div
        ref={boxRef}
        data-lanes
        className="relative min-h-target cursor-pointer rounded-row bg-head"
        style={{ height: count * lane }}
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const bar = at(e.clientX - r.left, e.clientY - r.top);
          setHover(bar && { bar, x: e.clientX - r.left, y: e.clientY - r.top });
        }}
        onPointerLeave={() => {
          setHover(undefined);
        }}
        onClick={(e) => {
          // A tap has no pointer moving before it: find the bar where it is.
          const r = e.currentTarget.getBoundingClientRect();
          const bar = at(e.clientX - r.left, e.clientY - r.top);
          if (bar) onSelect({ kind: "container", id: bar.container_id });
        }}
      >
        {width > 0 && (
          <svg
            width={width}
            height={count * lane}
            aria-hidden
            className="absolute inset-0"
          >
            {bars.map((bar, i) => {
              const x0 = Math.max(0, x(bar.start_t));
              const x1 = Math.min(width, Math.max(x(bar.last_t), x0 + least));
              const on =
                selected?.kind === "container" &&
                selected.id === bar.container_id;
              return (
                <rect
                  key={bar.container_id}
                  x={x0}
                  y={(assigned[i] ?? 0) * lane + 1}
                  width={Math.max(least, x1 - x0)}
                  height={lane - 2}
                  className={`${
                    on ? "fill-fg" : failed(bar) ? "fill-bad" : "fill-bar"
                  } ${hover?.bar === bar ? "stroke-fg" : ""}`}
                />
              );
            })}
          </svg>
        )}
      </div>
      {hover && (
        <Tip project={project} bar={hover.bar} x={hover.x} y={hover.y} />
      )}
    </div>
  );
}

function Tip({
  project,
  bar,
  x,
  y,
}: {
  project: Project;
  bar: Bar;
  x: number;
  y: number;
}) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 w-max max-w-md rounded-row border border-line bg-card px-3 py-2 text-xs shadow-sm"
      style={{
        left: x + px("--spacing-tip-offset"),
        top: y + px("--spacing-tip-offset"),
      }}
    >
      <p className="font-medium text-fg">{barLabel(project, bar)}</p>
      <p className="text-fg-3">{bar.image}</p>
      <p className="text-fg-2 tabular-nums">
        {instant(bar.start_t)} – {instant(bar.last_t)} (
        {duration(bar.last_t - bar.start_t)})
      </p>
      <p className="text-fg-2">{ending(bar)}</p>
      <p className="text-fg-3">Click to draw its curve.</p>
    </div>
  );
}

function ContainerList({
  project,
  bars,
  selected,
  onSelect,
}: {
  project: Project;
  bars: Bar[];
  selected: Selected | null;
  onSelect: (s: Selected) => void;
}) {
  return (
    <table className="mt-2 w-full text-xs">
      <thead className="text-left text-fg-3">
        <tr>
          <th className="px-2 py-1 font-medium">Container</th>
          <th className="px-2 py-1 font-medium">Image</th>
          <th className="px-2 py-1 text-right font-medium">Started</th>
          <th className="px-2 py-1 text-right font-medium">Ran</th>
          <th className="px-2 py-1 font-medium">End</th>
        </tr>
      </thead>
      <tbody>
        {bars.map((bar) => {
          const on =
            selected?.kind === "container" && selected.id === bar.container_id;
          return (
            <tr key={bar.container_id} className="border-t border-line-subtle">
              <td className="px-2 py-0.5">
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => {
                    onSelect({ kind: "container", id: bar.container_id });
                  }}
                  className={`${control} ${on ? "bg-active text-accent" : "text-fg"}`}
                >
                  {barLabel(project, bar)}
                </button>
              </td>
              <td className="px-2 py-0.5 break-all text-fg-3">{bar.image}</td>
              <td className="px-2 py-0.5 text-right tabular-nums">
                {instant(bar.start_t)}
              </td>
              <td className="px-2 py-0.5 text-right tabular-nums">
                {duration(bar.last_t - bar.start_t)}
              </td>
              <td
                className={`px-2 py-0.5 ${failed(bar) ? "text-bad" : "text-fg-2"}`}
              >
                {ending(bar)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
