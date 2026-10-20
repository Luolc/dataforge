import { Fragment, useId } from "react";
import { Link } from "react-router";

import type { Tone } from "../../../api/types";
import { toneGlyph } from "../../../ui/status";
import type { Layout } from "./graph";

/** The height of a line of an edge's label, in the graph's units. */
const LINE = 12;

const dot: Record<Tone, string> = {
  good: "fill-good",
  bad: "fill-bad",
  warn: "fill-warn",
  neutral: "fill-neutral",
};

/**
 * The machine as laid out: transitions not taken dashed, taken ones solid
 * with their counts; a state links to its last run.
 */
export function Graph({
  layout,
  runHref,
}: {
  layout: Layout;
  runHref: (index: number) => string;
}) {
  const id = useId();
  const head = (on: boolean) => `url(#${id}-${on ? "on" : "off"})`;
  return (
    <svg
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      role="group"
      aria-label="State machine"
      className="block h-auto w-full font-sans"
    >
      <defs>
        {[true, false].map((on) => (
          <marker
            key={String(on)}
            id={`${id}-${on ? "on" : "off"}`}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path
              d="M0 0 L8 4 L0 8 z"
              className={on ? "fill-fg-2" : "fill-faint"}
            />
          </marker>
        ))}
      </defs>
      {layout.edges.map((edge) => (
        <g key={edge.key} data-taken={edge.taken}>
          <path
            d={edge.path}
            markerEnd={head(edge.taken)}
            className={
              edge.taken
                ? "fill-none stroke-fg-2 stroke-[length:var(--stroke-taken)]"
                : "fill-none stroke-faint stroke-1 [stroke-dasharray:var(--dash-not-taken)]"
            }
          />
          <text
            x={edge.label.x}
            y={edge.label.y}
            textAnchor="middle"
            className="fill-faint text-graph"
          >
            {/* One outcome a line, the last on the label's baseline. */}
            {edge.label.parts.map((part, i, all) => (
              <tspan
                key={part.outcome}
                x={edge.label.x}
                y={edge.label.y - (all.length - 1 - i) * LINE}
                className={part.taken > 0 ? "fill-fg font-semibold" : ""}
              >
                {part.outcome}
                {part.taken > 1 && ` ×${part.taken}`}
              </tspan>
            ))}
          </text>
        </g>
      ))}
      {layout.nodes.map((node) => {
        const box = (
          <g
            transform={`translate(${node.x} ${node.y})`}
            className={node.idle ? "opacity-[var(--opacity-idle)]" : ""}
          >
            <rect
              width={node.width}
              height={node.height}
              className="fill-panel stroke-line [rx:var(--radius-panel)]"
            />
            {node.tone !== null && <ToneMark tone={node.tone} />}
            <text x="28" y="23" className="fill-fg text-sm font-semibold">
              {node.title}
            </text>
            <text x="16" y="40" className="fill-fg-3 text-2xs">
              {node.sub}
            </text>
          </g>
        );
        return node.lastVisit === null ? (
          <Fragment key={node.key}>{box}</Fragment>
        ) : (
          <Link
            key={node.key}
            to={runHref(node.lastVisit)}
            aria-label={`${node.title}: its last run`}
          >
            {box}
          </Link>
        );
      })}
    </svg>
  );
}

/** A node's tone as a filled circle with the tone's glyph (L39). */
function ToneMark({ tone }: { tone: Tone }) {
  const Glyph = toneGlyph[tone];
  return (
    <>
      <circle cx="16" cy="19" r="7" className={dot[tone]} />
      <Glyph
        x="11.5"
        y="14.5"
        width="9"
        height="9"
        strokeWidth={3.5}
        className="text-on-tone"
        aria-hidden
      />
    </>
  );
}
