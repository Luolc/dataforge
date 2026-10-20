import { CircleDashed, Maximize, Minimize, Minus, Plus } from "lucide-react";
import {
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

import type { Project } from "../../../api/types";
import { StatusIcon } from "../../../ui/status-mark";
import type { Edge, Layout } from "./graph";

/** The height of a line of an edge's label, in the graph's units. */
const LINE = 14;
/** The scales the buttons step through, and the bounds of every zoom. */
const MIN = 0.25;
const MAX = 2;
const STEP = 1.25;
/** The scale the graph starts at outside full screen; wider, it pans. */
const START = 1;
/** Further than this, a pointer drags the graph instead of clicking. */
const DRAG = 4;

interface View {
  k: number;
  x: number;
  y: number;
}

const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));

/**
 * The machine as laid out, in a frame that zooms and pans: transitions not
 * taken dashed, taken ones solid in the accent with their counts. Hovering or
 * focusing a state lights its edges and the states they join and dims the
 * rest; a state links to its last run.
 */
export function Graph({
  project,
  layout,
  runHref,
}: {
  project: Project;
  layout: Layout;
  runHref: (index: number) => string;
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

  // The view that shows the whole graph, or its left part at START.
  const fit = useCallback(
    (w: number, h: number, whole: boolean): View => {
      const across = w / layout.width;
      const k = whole
        ? clamp(Math.min(across, h / layout.height), START, 1.5)
        : START;
      return {
        k,
        x: Math.max(0, (w - layout.width * k) / 2),
        y: whole ? Math.max(0, (h - layout.height * k) / 2) : 0,
      };
    },
    [layout],
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
      style={height === undefined ? undefined : { height }}
      className={`relative touch-pan-y overflow-hidden bg-app select-none ${full ? "h-full w-full" : "rounded-b-panel"}`}
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
        </defs>
        <g transform={`translate(${shown.x} ${shown.y}) scale(${shown.k})`}>
          {edges.map((edge) => (
            <EdgeView
              key={edge.key}
              edge={edge}
              head={`url(#${id}-head)`}
              lit={lit(edge)}
              dim={active !== null && !lit(edge)}
            />
          ))}
          {layout.nodes.map((node) => {
            const card = (
              <NodeCard
                project={project}
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
            return node.lastVisit === null ? (
              <g key={node.key} data-node={node.key} {...events}>
                {card}
              </g>
            ) : (
              <Link
                key={node.key}
                to={runHref(node.lastVisit)}
                aria-label={`${accessibleName(node)}: open its last run`}
                data-node={node.key}
                {...events}
              >
                {card}
              </Link>
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
  lit,
  dim,
}: {
  edge: Edge;
  head: string;
  lit: boolean;
  dim: boolean;
}) {
  const stroke = edge.taken
    ? `stroke-accent ${lit ? "stroke-[2.5]" : "stroke-2"}`
    : lit
      ? "stroke-fg stroke-[1.5] [stroke-dasharray:var(--dash-not-taken)]"
      : "stroke-icon stroke-1 [stroke-dasharray:var(--dash-not-taken)]";
  const { label } = edge;
  return (
    <g
      data-edge={edge.key}
      data-taken={edge.taken}
      data-lit={lit}
      className={dim ? "opacity-[var(--opacity-idle)]" : ""}
    >
      <path
        d={edge.path}
        {...(edge.back ? { markerStart: head } : { markerEnd: head })}
        className={`fill-none ${stroke}`}
      />
      <text
        x={label.x}
        y={label.y}
        textAnchor={label.anchor}
        className="text-xs"
      >
        {label.parts.map((part, i, all) => (
          <tspan
            key={part.outcome}
            {...(label.stack
              ? { x: label.x, y: label.y - (all.length - 1 - i) * LINE }
              : {})}
            className={
              part.taken > 0
                ? "fill-fg font-semibold"
                : lit
                  ? "fill-fg-2"
                  : "fill-fg-3"
            }
          >
            {!label.stack && i > 0 && ", "}
            {part.outcome}
            {part.taken > 0 && ` ×${part.taken}`}
          </tspan>
        ))}
      </text>
    </g>
  );
}

/** A state's title, status, runs and time, as a screen reader reads it. */
function accessibleName(node: Layout["nodes"][number]): string {
  return [node.title, node.status?.label, node.sub, node.time]
    .filter((part) => part != null)
    .join(", ");
}

function NodeCard({
  project,
  node,
  lit,
  dim,
}: {
  project: Project;
  node: Layout["nodes"][number];
  lit: boolean;
  dim: boolean;
}) {
  const border = lit
    ? "stroke-fg stroke-2"
    : node.idle
      ? "stroke-icon [stroke-dasharray:var(--dash-not-taken)]"
      : "stroke-icon";
  return (
    <g
      transform={`translate(${node.x} ${node.y})`}
      className={dim ? "opacity-[var(--opacity-idle)]" : ""}
    >
      <rect
        width={node.width}
        height={node.height}
        className={`${node.idle ? "fill-app" : "fill-panel"} ${border} [rx:var(--radius-panel)]`}
      />
      {[0, node.width].map((cx) => (
        <circle
          key={cx}
          cx={cx}
          cy={node.height / 2}
          r="3.5"
          className="fill-app stroke-icon"
        />
      ))}
      {node.status !== null ? (
        <foreignObject x="12" y="12" width="16" height="16">
          <StatusIcon project={project} word={node.word} ended={node.ended} />
        </foreignObject>
      ) : (
        // As big as a StatusIcon, its stroke as wide as that icon's glyph
        // draws (3 at 10px wide is about 2 at 16px).
        <CircleDashed
          x="12"
          y="12"
          width="16"
          height="16"
          strokeWidth={2}
          className="text-icon"
          aria-hidden
        />
      )}
      <text
        x="36"
        y="25"
        className={`text-md font-semibold ${node.idle ? "fill-fg-3" : "fill-fg"}`}
      >
        {node.title}
      </text>
      {node.time !== null && (
        <text
          x={node.width - 14}
          y="25"
          textAnchor="end"
          className="fill-fg-3 text-xs [font-variant-numeric:tabular-nums]"
        >
          {node.time}
        </text>
      )}
      <text x="36" y="43" className="fill-fg-3 text-xs">
        {node.sub}
      </text>
    </g>
  );
}
