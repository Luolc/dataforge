import { CircleDashed, Maximize, Minimize, Minus, Plus } from "lucide-react";
import { Popover } from "radix-ui";
import {
  Fragment,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router";

import { Elapsed, RunningIcon } from "../../../ui/running";
import { ToneIcon } from "../../../ui/status-mark";
import {
  CHIP_HEIGHT,
  type Edge,
  labelText,
  type Layout,
  SUB_LINE,
  textTop,
  TITLE_LINE,
} from "./graph";

/** The scales the buttons step through, and the bounds of every zoom. */
const MIN = 0.25;
const MAX = 2;
const STEP = 1.25;
/** The scale the graph starts at outside full screen; wider, it pans. */
const START = 1;
/** Further than this, a pointer drags the graph instead of clicking. */
const DRAG = 4;
/**
 * How many dashes of a pulse are laid over one another, all ending at its
 * head, so that the part of it its tail token gives fades in as many steps.
 */
const TAIL = 12;

interface View {
  k: number;
  x: number;
  y: number;
}

const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));

/**
 * The machine as laid out, in a frame that zooms and pans: transitions not
 * taken dashed, taken ones solid in the accent with links to the runs that
 * took them. Hovering or focusing a state lights its edges and the states
 * they join and dims the rest, and pulses run along the lit edges the
 * attempt may still take or took, from the state each leaves to the one it
 * goes to; a state links to its last run, and one that ran more than once
 * lists its runs.
 */
export function Graph({
  layout,
  runHref,
  frameId,
}: {
  layout: Layout;
  runHref: (index: number) => string;
  /** The frame's ID, for a control that changes what it draws. */
  frameId?: string;
}) {
  const id = useId();
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [full, setFull] = useState(false);
  const [view, setView] = useState<View | null>(null);
  // The state under the pointer wins over the one with the focus.
  const [hovered, setHovered] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const active = hovered ?? focused;
  // The state the attempt ran in when the graph was first shown: the page
  // reading the attempt again does not move the view to where it runs now.
  const [centred] = useState(
    () => layout.nodes.find((n) => n.running !== null)?.key ?? null,
  );

  // The view that shows the whole graph, or a part of it at START: the
  // state the attempt ran in when first shown in the middle, else the left
  // part.
  const fit = useCallback(
    (w: number, h: number, whole: boolean): View => {
      const across = w / layout.width;
      const k = whole
        ? clamp(Math.min(across, h / layout.height), START, 1.5)
        : START;
      const gw = layout.width * k;
      const middle = layout.nodes.find((n) => n.key === centred);
      const x =
        gw <= w
          ? (w - gw) / 2
          : middle && !whole
            ? clamp(w / 2 - (middle.x + middle.width / 2) * k, w - gw, 0)
            : 0;
      return {
        k,
        x,
        y: whole ? Math.max(0, (h - layout.height * k) / 2) : 0,
      };
    },
    [layout, centred],
  );

  // Keep at least part of the graph in the frame.
  const bound = useCallback(
    (v: View): View => {
      const k = clamp(v.k, MIN, MAX);
      const gw = layout.width * k;
      const gh = layout.height * k;
      const keep = (pos: number, frameSide: number, side: number) =>
        side <= frameSide
          ? clamp(pos, 0, frameSide - side)
          : clamp(pos, frameSide - side, 0);
      return { k, x: keep(v.x, size.w, gw), y: keep(v.y, size.h, gh) };
    },
    [layout, size],
  );

  useLayoutEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const onChange = () => {
      setFull(document.fullscreenElement === frameRef.current);
      setView(null);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
    };
  }, []);

  // The frame's height outside full screen: the graph at its starting scale.
  const startK = fit(size.w, 0, false).k;
  // Room below for the buttons.
  const height = full ? undefined : Math.ceil(layout.height * startK) + 48;
  const shown = useMemo(
    () =>
      view ?? (size.w > 0 ? fit(size.w, size.h, full) : { k: 1, x: 0, y: 0 }),
    [view, size, fit, full],
  );

  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number) => {
      setView((v) => {
        const cur = v ?? shown;
        const k = clamp(cur.k * factor, MIN, MAX);
        const r = k / cur.k;
        return bound({ k, x: cx - (cx - cur.x) * r, y: cy - (cy - cur.y) * r });
      });
    },
    [bound, shown],
  );

  // Ctrl or Cmd and the wheel zoom at the pointer; a sideways wheel pans. A
  // plain wheel scrolls the page.
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const rect = el.getBoundingClientRect();
        zoomAt(
          Math.exp(-e.deltaY / 300),
          e.clientX - rect.left,
          e.clientY - rect.top,
        );
      } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        setView((v) =>
          bound({ ...(v ?? shown), x: (v ?? shown).x - e.deltaX }),
        );
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("wheel", onWheel);
    };
  }, [zoomAt, bound, shown]);

  const dragRef = useRef<{ x: number; y: number; from: View; moved: boolean }>(
    null,
  );
  const onPointerDown = (e: ReactPointerEvent) => {
    if (
      e.button !== 0 ||
      (e.target instanceof Element && e.target.closest("button") !== null)
    )
      return;
    dragRef.current = { x: e.clientX, y: e.clientY, from: shown, moved: false };
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < DRAG) return;
    if (!d.moved) frameRef.current?.setPointerCapture(e.pointerId);
    d.moved = true;
    setView(bound({ k: d.from.k, x: d.from.x + dx, y: d.from.y + dy }));
  };
  // A drag that ends on a state does not open it. The click a drag may
  // bring comes right after its release; a touch drag brings none, so each
  // release sets the mark afresh.
  const draggedRef = useRef(false);
  const onPointerUp = () => {
    draggedRef.current = dragRef.current?.moved === true;
    dragRef.current = null;
  };
  const onClickCapture = (e: React.MouseEvent) => {
    if (!draggedRef.current) return;
    draggedRef.current = false;
    e.preventDefault();
    e.stopPropagation();
  };

  // A state that gets the keyboard's focus outside the frame comes into it.
  const reveal = (node: Layout["nodes"][number]) => {
    const { k, x, y } = shown;
    const left = node.x * k + x;
    const right = (node.x + node.width) * k + x;
    if (left >= 0 && right <= size.w) return;
    const nx = left < 0 ? x - left + 16 : x - (right - size.w) - 16;
    setView(bound({ k, x: nx, y }));
  };

  const lit = (e: Edge) =>
    active !== null && (e.from === active || e.to === active);
  const near = new Set(
    active === null
      ? []
      : layout.edges
          .filter(lit)
          .flatMap((e) => [e.from, e.to])
          .concat(active),
  );
  const dim = (key: string) => active !== null && !near.has(key);
  // Taken edges over the rest, so that a lane they share stays solid only
  // where an edge was taken, then lit edges over unlit ones.
  const edges = [...layout.edges].sort((a, b) => {
    const byTaken = Number(a.taken) - Number(b.taken);
    return byTaken !== 0 ? byTaken : Number(lit(a)) - Number(lit(b));
  });

  const toggleFull = () => {
    // A refused request leaves the frame as it is.
    const ignore = () => undefined;
    if (document.fullscreenElement) document.exitFullscreen().catch(ignore);
    else frameRef.current?.requestFullscreen().catch(ignore);
  };
  const center = () => [size.w / 2, size.h / 2] as const;

  return (
    <div
      ref={frameRef}
      id={frameId}
      style={height === undefined ? undefined : { height }}
      className={`relative touch-pan-y overflow-hidden bg-card select-none ${full ? "h-full w-full" : "rounded-b-panel"}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onClickCapture={onClickCapture}
    >
      <svg
        role="group"
        aria-label="State machine"
        className="absolute inset-0 block h-full w-full font-sans"
      >
        <defs>
          <marker
            id={`${id}-head`}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            markerUnits="userSpaceOnUse"
            orient="auto-start-reverse"
          >
            <path d="M0 0 L8 4 L0 8 z" fill="context-stroke" />
          </marker>
          <marker
            id={`${id}-head-lit`}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="9"
            markerHeight="9"
            markerUnits="userSpaceOnUse"
            orient="auto-start-reverse"
          >
            <path d="M0 0 L8 4 L0 8 z" fill="context-stroke" />
          </marker>
        </defs>
        <g transform={`translate(${shown.x} ${shown.y}) scale(${shown.k})`}>
          {edges.map((edge) => (
            <EdgeView
              key={edge.key}
              edge={edge}
              head={`url(#${id}-head${lit(edge) ? "-lit" : ""})`}
              runHref={runHref}
              lit={lit(edge)}
              dim={active !== null && !lit(edge)}
              // Once the attempt has ended, only what it took is left to
              // show the way of; a stub names where it goes.
              pulse={lit(edge) && !edge.stub && (edge.taken || !layout.ended)}
              box={layout}
            />
          ))}
          {layout.nodes.map((node) => {
            const card = (
              <NodeCard
                node={node}
                lit={active === node.key}
                dim={dim(node.key)}
              />
            );
            const events = {
              onPointerEnter: () => {
                setHovered(node.key);
              },
              onPointerLeave: () => {
                setHovered(null);
              },
              onFocus: () => {
                setFocused(node.key);
                reveal(node);
              },
              onBlur: () => {
                setFocused(null);
              },
            };
            return (
              <Fragment key={node.key}>
                {node.lastVisit === null ? (
                  <g data-node={node.key} {...events}>
                    {card}
                  </g>
                ) : (
                  <Link
                    to={runHref(node.lastVisit)}
                    aria-label={`${accessibleName(node)}: open its last run`}
                    data-node={node.key}
                    {...events}
                  >
                    {card}
                  </Link>
                )}
                <RunsList
                  node={node}
                  target={layout.target}
                  runHref={runHref}
                />
              </Fragment>
            );
          })}
        </g>
      </svg>
      <div className="absolute right-3 bottom-3 flex gap-2">
        {document.fullscreenEnabled && (
          <ControlButton
            label={full ? "Exit full screen" : "Full screen"}
            onClick={toggleFull}
          >
            {full ? <Minimize size={16} /> : <Maximize size={16} />}
          </ControlButton>
        )}
        <div className="flex">
          <ControlButton
            label="Zoom out"
            onClick={() => {
              zoomAt(1 / STEP, ...center());
            }}
            disabled={shown.k <= MIN}
            joined="left"
          >
            <Minus size={16} />
          </ControlButton>
          <ControlButton
            label="Zoom in"
            onClick={() => {
              zoomAt(STEP, ...center());
            }}
            disabled={shown.k >= MAX}
            joined="right"
          >
            <Plus size={16} />
          </ControlButton>
        </div>
      </div>
    </div>
  );
}

function ControlButton({
  label,
  onClick,
  disabled = false,
  joined,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  joined?: "left" | "right";
  children: React.ReactNode;
}) {
  const corners =
    joined === "left"
      ? "rounded-l-row border-r-0"
      : joined === "right"
        ? "rounded-r-row"
        : "rounded-row";
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className={`flex size-8 target items-center justify-center border border-line bg-panel text-icon hover:bg-hover hover:text-fg disabled:text-faint ${corners}`}
    >
      {children}
    </button>
  );
}

function EdgeView({
  edge,
  head,
  runHref,
  lit,
  dim,
  pulse,
  box,
}: {
  edge: Edge;
  head: string;
  runHref: (index: number) => string;
  lit: boolean;
  dim: boolean;
  pulse: boolean;
  box: { width: number; height: number };
}) {
  const stroke = edge.taken
    ? `stroke-accent ${lit ? "[stroke-width:var(--stroke-lit)]" : "stroke-2"}`
    : lit
      ? "stroke-fg [stroke-width:var(--stroke-lit-not-taken)] [stroke-dasharray:var(--dash-not-taken)]"
      : "stroke-icon stroke-1 [stroke-dasharray:var(--dash-not-taken)]";
  const { label } = edge;
  return (
    <g
      data-edge={edge.key}
      data-taken={edge.taken}
      data-stub={edge.stub}
      data-lit={lit}
      className={dim ? "opacity-[var(--opacity-idle)]" : ""}
    >
      <path d={edge.path} markerEnd={head} className={`fill-none ${stroke}`} />
      {pulse && <Pulse d={edge.path} taken={edge.taken} box={box} />}
      <text x={label.x} y={label.y} className="text-xs">
        {label.lines.map((line) => {
          const part = label.parts[line.part];
          if (part === undefined || line.text === "") return null;
          return (
            <tspan
              key={`${part.outcome}-${line.top.toString()}`}
              x={label.x}
              y={line.y}
              className={
                part.taken > 0
                  ? "fill-fg font-semibold"
                  : lit
                    ? "fill-fg-2"
                    : "fill-fg-3"
              }
            >
              {line.text}
            </tspan>
          );
        })}
      </text>
      {label.lines.map((line) => {
        const part = label.parts[line.part];
        if (part === undefined) return null;
        return line.chips.map((chip) => {
          const n = (chip.index + 1).toString();
          const middle = chip.x + chip.width / 2;
          return (
            <Link
              key={`${part.outcome}-${n}`}
              to={runHref(chip.index)}
              aria-label={`${labelText(part)}: open run ${n}`}
              data-run={chip.index}
              className="group"
            >
              <rect
                x={chip.x}
                y={line.top}
                width={chip.width}
                height={line.height}
                className="fill-transparent"
              />
              <rect
                x={middle - chip.pill / 2}
                y={line.top + (line.height - CHIP_HEIGHT) / 2}
                width={chip.pill}
                height={CHIP_HEIGHT}
                className="fill-panel stroke-line [rx:var(--radius-row)] group-hover:fill-hover group-hover:stroke-fg-3"
              />
              <text
                x={middle}
                y={line.y}
                textAnchor="middle"
                className="fill-fg text-xs font-semibold [font-variant-numeric:tabular-nums]"
              >
                {n}
              </text>
            </Link>
          );
        });
      })}
    </g>
  );
}

/**
 * The control on a card that ran more than once, over where the card names
 * how many times, which opens the list of its runs: each run's number in
 * the Runs tab and its outcome, a link to the run where it has a page.
 */
function RunsList({
  node,
  target,
  runHref,
}: {
  node: Layout["nodes"][number];
  target: number;
  runHref: (index: number) => string;
}) {
  const at = node.runsText;
  if (at === null) return null;
  // The first line under the title, or the running card's first live line,
  // which starts at the card's padding.
  const top = textTop(node) + node.lines.length * TITLE_LINE + 12;
  const left = node.running ? 12 : 36;
  const width = Math.max(target, Math.ceil(at.width) + 8);
  // Centred on the line's text, as tall as a target.
  const y = node.y + top - 4 - target / 2;
  const x = node.x + left - 4;
  return (
    <>
      <line
        x1={node.x + left}
        x2={node.x + left + at.width}
        y1={node.y + top + 3}
        y2={node.y + top + 3}
        aria-hidden
        className="pointer-events-none stroke-fg-3 [stroke-dasharray:var(--dash-not-taken)]"
      />
      <foreignObject x={x} y={y} width={width} height={target}>
        <Popover.Root>
          <Popover.Trigger
            aria-label={`${node.title}: ${at.text}, list them`}
            data-runs={node.key}
            className="block size-full rounded-row hover:outline-1 hover:outline-fg-3 data-[state=open]:outline-1 data-[state=open]:outline-fg-3"
          />
          <Popover.Portal>
            <Popover.Content
              side="bottom"
              align="start"
              sideOffset={4}
              collisionPadding={8}
              aria-label={`${node.title}: its runs`}
              onPointerDown={(e) => {
                e.stopPropagation();
              }}
              className="z-10 flex max-h-(--radix-popover-content-available-height) min-w-48 flex-col gap-0.5 overflow-y-auto rounded-panel border border-line bg-panel p-1.5 text-fg"
            >
              <p className="px-2 py-1 text-xs text-fg-3">
                {node.title} · {node.runs.length} runs
              </p>
              <ul className="flex flex-col gap-0.5">
                {node.runs.map((run, i, all) => {
                  const line = (
                    <>
                      {node.running && i === all.length - 1 ? (
                        <RunningIcon />
                      ) : (
                        <ToneIcon label={run.label} tone={run.tone} />
                      )}
                      <span className="text-fg-3 tabular-nums">
                        Run {run.index + 1}
                      </span>
                      <span>{run.label}</span>
                    </>
                  );
                  const row =
                    "flex target items-center gap-2 rounded-row px-2 py-1 text-fg-2";
                  return (
                    <li key={run.index}>
                      {run.recorded ? (
                        <Link
                          to={runHref(run.index)}
                          className={`${row} hover:bg-hover hover:text-fg`}
                        >
                          {line}
                        </Link>
                      ) : (
                        <span className={row}>{line}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      </foreignObject>
    </>
  );
}

/**
 * Pulses along a path, from its start to its end at the same speed on every
 * edge: one leaves the start each period, so that a long edge has several
 * on it at once. A mask that opens from the start at that speed keeps those
 * of the pattern that have not left yet out of sight. When the reader asks
 * for reduced motion, they stand along the whole path, their tails behind
 * them.
 */
function Pulse({
  d,
  taken,
  box,
}: {
  d: string;
  taken: boolean;
  box: { width: number; height: number };
}) {
  const id = useId();
  // Room for a loop above the top card and the stroke beyond the sides.
  const m = 64;
  return (
    <g aria-hidden className="pointer-events-none">
      <mask
        id={id}
        maskUnits="userSpaceOnUse"
        x={-m}
        y={-m}
        width={box.width + 2 * m}
        height={box.height + 2 * m}
        className="[mask-type:alpha]"
      >
        <path d={d} className="pulse-open fill-none stroke-fg stroke-[32]" />
      </mask>
      <g mask={`url(#${id})`}>
        {taken && (
          <g className="pulse-glow stroke-pulse-glow">
            <Comet d={d} />
          </g>
        )}
        <g
          className={
            taken
              ? "stroke-pulse"
              : "stroke-pulse-not-taken [--pulse-width:var(--pulse-width-not-taken)]"
          }
        >
          <Comet d={d} />
        </g>
      </g>
    </g>
  );
}

/**
 * A pulse's dashes, all ending at its head, each longer than the one before
 * and fainter, so that together they fade evenly from the head to the tail.
 */
function Comet({ d }: { d: string }) {
  return Array.from({ length: TAIL }, (_, i) => (
    <path
      key={i}
      d={d}
      className="pulse fill-none"
      style={
        {
          "--pulse-dash": `calc(var(--pulse-length) * (1 - var(--pulse-tail) * ${String((TAIL - 1 - i) / TAIL)}))`,
          opacity: 1 / (i + 1),
        } as React.CSSProperties
      }
    />
  ));
}

/** A state's title, status, runs and time, as a screen reader reads it. */
function accessibleName(node: Layout["nodes"][number]): string {
  return [node.title, node.status?.label, node.sub, node.time]
    .filter((part) => part != null)
    .join(", ");
}

function NodeCard({
  node,
  lit,
  dim,
}: {
  node: Layout["nodes"][number];
  lit: boolean;
  dim: boolean;
}) {
  const border = lit
    ? "stroke-fg stroke-2"
    : node.running
      ? "stroke-accent stroke-2"
      : node.idle
        ? "stroke-icon [stroke-dasharray:var(--dash-not-taken)]"
        : "stroke-icon";
  const top = textTop(node);
  const icon = top + 1;
  const under = top + node.lines.length * TITLE_LINE + 12;
  return (
    <g
      transform={`translate(${node.x} ${node.y})`}
      className={dim ? "opacity-[var(--opacity-idle)]" : ""}
    >
      <rect
        width={node.width}
        height={node.height}
        className={`${node.idle ? "fill-app" : "fill-card"} ${border} [rx:var(--radius-panel)]`}
      />
      {node.ports.map(([cx, cy]) => (
        <circle
          key={`${String(cx)} ${String(cy)}`}
          cx={cx}
          cy={cy}
          r="3.5"
          className="fill-app stroke-icon"
        />
      ))}
      {node.running ? (
        <foreignObject x="12" y={icon} width="16" height="16">
          <RunningIcon />
        </foreignObject>
      ) : node.status !== null ? (
        <foreignObject x="12" y={icon} width="16" height="16">
          <ToneIcon {...node.status} />
        </foreignObject>
      ) : (
        // As big as a StatusIcon, its stroke as wide as that icon's glyph
        // draws (3 at 10px wide is about 2 at 16px).
        <CircleDashed
          x="12"
          y={icon}
          width="16"
          height="16"
          strokeWidth={2}
          className="text-icon"
          aria-hidden
        />
      )}
      <text
        className={`text-md font-semibold ${node.idle ? "fill-fg-3" : "fill-fg"}`}
      >
        {node.lines
          .map((line, i) => ({ line, y: top + 13 + i * TITLE_LINE }))
          .map(({ line, y }) => (
            <tspan key={y} x="36" y={y}>
              {line}
            </tspan>
          ))}
      </text>
      <text
        xmlSpace="preserve"
        className="fill-fg-3 text-xs [font-variant-numeric:tabular-nums]"
      >
        {node.under
          .map((line, i) => ({ line, y: under + i * SUB_LINE }))
          .map(({ line, y }) => (
            <tspan key={y} x="36" y={y}>
              {line}
            </tspan>
          ))}
        {node.running?.rows.map((row, i) => {
          const y = under + (node.under.length + i) * SUB_LINE;
          // An empty tspan does not move the pen: the row starts at its clock.
          const at = row.lead === "" ? { x: 12, y } : {};
          return (
            <Fragment key={y}>
              {row.lead !== "" && (
                <tspan x="12" y={y}>
                  {row.lead}
                </tspan>
              )}
              {row.live !== null && node.running?.since != null && (
                <tspan {...at}>
                  <Elapsed
                    since={node.running.since}
                    before={row.live === "all" ? node.running.before : 0}
                  />
                </tspan>
              )}
              {row.tail !== "" && <tspan>{row.tail}</tspan>}
            </Fragment>
          );
        })}
      </text>
    </g>
  );
}
