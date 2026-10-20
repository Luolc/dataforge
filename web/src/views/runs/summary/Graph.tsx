import { Maximize, Minimize, Minus, Plus } from "lucide-react";
import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Link } from "react-router";

import type { Tone } from "../../../api/types";
import { toneGlyph } from "../../../ui/status";
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

const dot: Record<Tone, string> = {
  good: "fill-good",
  bad: "fill-bad",
  warn: "fill-warn",
  neutral: "fill-neutral",
};

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
  layout,
  runHref,
}: {
  layout: Layout;
  runHref: (index: number) => string;
}) {
  const id = useId();
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [full, setFull] = useState(false);
  const [view, setView] = useState<View | null>(null);
  const [active, setActive] = useState<string | null>(null);

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
    const el = frame.current;
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
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onChange = () => {
      setFull(document.fullscreenElement === frame.current);
      setView(null);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // The frame's height outside full screen: the graph at its starting scale.
  const startK = fit(size.w, 0, false).k;
  // Room below for the buttons.
  const height = full ? undefined : Math.ceil(layout.height * startK) + 48;
  const shown =
    view ?? (size.w > 0 ? fit(size.w, size.h, full) : { k: 1, x: 0, y: 0 });

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
    const el = frame.current;
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
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt, bound, shown]);

  const drag = useRef<{ x: number; y: number; from: View; moved: boolean }>(
    null,
  );
  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.button !== 0 || (e.target as Element).closest("button")) return;
    drag.current = { x: e.clientX, y: e.clientY, from: shown, moved: false };
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < DRAG) return;
    if (!d.moved) frame.current?.setPointerCapture(e.pointerId);
    d.moved = true;
    setView(bound({ k: d.from.k, x: d.from.x + dx, y: d.from.y + dy }));
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    // A drag that ends on a state does not open it.
    if (d?.moved) {
      frame.current?.addEventListener(
        "click",
        (c) => {
          c.preventDefault();
          c.stopPropagation();
        },
        { capture: true, once: true },
      );
    }
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
  const edges = [...layout.edges].sort(
    (a, b) =>
      Number(a.taken) - Number(b.taken) || Number(lit(a)) - Number(lit(b)),
  );

  const toggleFull = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void frame.current?.requestFullscreen();
  };
  const center = () => [size.w / 2, size.h / 2] as const;

  return (
    <div
      ref={frame}
      style={height === undefined ? undefined : { height }}
      className={`relative touch-pan-y overflow-hidden bg-app select-none ${full ? "h-full w-full" : "rounded-b-panel"}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
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
                node={node}
                lit={active === node.key}
                dim={dim(node.key)}
              />
            );
            const events = {
              onPointerEnter: () => setActive(node.key),
              onPointerLeave: () => setActive(null),
              onFocus: () => {
                setActive(node.key);
                reveal(node);
              },
              onBlur: () => setActive(null),
            };
            return node.lastVisit === null ? (
              <g key={node.key} data-node={node.key} {...events}>
                {card}
              </g>
            ) : (
              <Link
                key={node.key}
                to={runHref(node.lastVisit)}
                aria-label={`${node.title}: its last run`}
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
        <ControlButton
          label={full ? "Exit full screen" : "Full screen"}
          onClick={toggleFull}
        >
          {full ? <Minimize size={16} /> : <Maximize size={16} />}
        </ControlButton>
        <div className="flex">
          <ControlButton
            label="Zoom out"
            onClick={() => zoomAt(1 / STEP, ...center())}
            disabled={shown.k <= MIN}
            joined="left"
          >
            <Minus size={16} />
          </ControlButton>
          <ControlButton
            label="Zoom in"
            onClick={() => zoomAt(STEP, ...center())}
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
      {node.tone !== null ? (
        <ToneMark tone={node.tone} />
      ) : (
        <circle
          cx="20"
          cy="20"
          r="7"
          className="fill-none stroke-icon [stroke-dasharray:var(--dash-not-taken)]"
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

/** A node's tone as a filled circle with the tone's glyph (L39). */
function ToneMark({ tone }: { tone: Tone }) {
  const Glyph = toneGlyph[tone];
  return (
    <>
      <circle cx="20" cy="20" r="8" className={dot[tone]} />
      <Glyph
        x="14.5"
        y="14.5"
        width="11"
        height="11"
        strokeWidth={3.5}
        className="text-on-tone"
        aria-hidden
      />
    </>
  );
}
