import { Fragment, useId } from "react";
import { Link } from "react-router";

import type { Tone } from "../../../api/types";
import type { Layout } from "./graph";

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
      role="img"
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
                ? "fill-none stroke-fg-2 stroke-[1.5]"
                : "fill-none stroke-faint stroke-1 [stroke-dasharray:3_3]"
            }
          />
          <text
            x={edge.label.x}
            y={edge.label.y}
            textAnchor="middle"
            className="fill-faint text-[10.5px]"
          >
            {edge.label.parts.map((part, i) => (
              <Fragment key={part.outcome}>
                {i > 0 && <tspan> · </tspan>}
                <tspan
                  className={part.taken > 0 ? "fill-fg font-semibold" : ""}
                >
                  {part.outcome}
                  {part.taken > 1 && ` ×${part.taken}`}
                </tspan>
              </Fragment>
            ))}
            {edge.label.more > 0 && <tspan> · +{edge.label.more} more</tspan>}
          </text>
        </g>
      ))}
      {layout.nodes.map((node) => {
        const box = (
          <g
            transform={`translate(${node.x} ${node.y})`}
            className={node.idle ? "opacity-45" : ""}
          >
            <rect
              width={node.width}
              height={node.height}
              rx="8"
              className="fill-panel stroke-line"
            />
            <circle
              cx="16"
              cy="19"
              r="5"
              className={node.tone ? dot[node.tone] : "fill-neutral"}
            />
            <text x="28" y="23" className="fill-fg text-[13px] font-semibold">
              {node.title}
            </text>
            <text x="16" y="40" className="fill-fg-3 text-[11px]">
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
