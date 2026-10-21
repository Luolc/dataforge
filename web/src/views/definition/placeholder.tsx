// A template variable drawn in place in rendered Markdown, as a block
// with its name and, on an attempt's page, the value the attempt filled in.
// Hovering or focusing it shows where the value comes from; a click keeps
// that box open.
import { SlidersHorizontal } from "lucide-react";
import { useId, useState } from "react";

import type { Variable } from "./types";
import { EMPTY, PER_ROUND } from "./words";

export function Placeholder({
  name,
  spelled,
  variable,
  filled,
}: {
  name: string;
  /** The variable as the template writes it, such as ${name}. */
  spelled: string;
  /** Its row in the entry's variables; undefined when it has none. */
  variable: Variable | undefined;
  /** Whether to show the value the attempt filled in. */
  filled: boolean;
}) {
  const id = useId();
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const open = pinned || hovered;
  const value = variable?.value;
  const shown = !filled
    ? null
    : variable?.per_round === true
      ? { text: PER_ROUND, faint: true }
      : value === undefined
        ? null
        : value === ""
          ? { text: EMPTY, faint: true }
          : { text: value, faint: false };
  return (
    <span className="relative my-0.5 inline-block align-baseline">
      <button
        type="button"
        aria-label={shown === null ? name : `${name}: ${shown.text}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setPinned((p) => !p);
        }}
        onMouseEnter={() => {
          setHovered(true);
        }}
        onMouseLeave={() => {
          setHovered(false);
        }}
        onFocus={() => {
          setHovered(true);
        }}
        onBlur={() => {
          setHovered(false);
        }}
        className="inline-flex target items-center gap-1.5 rounded-code bg-code-gutter px-1.5 align-baseline font-mono text-code leading-none ring-1 ring-line-subtle hover:bg-hover aria-expanded:ring-line"
      >
        <span className="text-syn-variable">{name}</span>
        {shown !== null && (
          <span
            className={`max-w-56 truncate border-l border-line pr-0.5 pl-1.5 ${shown.faint ? "text-fg-3 italic" : "text-fg"}`}
          >
            {shown.text}
          </span>
        )}
        <SlidersHorizontal className="size-3.5 text-icon" aria-hidden />
      </button>
      {open && (
        <span
          id={id}
          role="note"
          className="absolute top-full left-0 z-10 mt-1 block"
        >
          <span className="block w-max max-w-md rounded-row border border-line bg-card px-3 py-2 font-sans text-xs leading-normal text-fg-2 shadow-sm">
            <span className="block">
              Variable{" "}
              <code className="font-mono text-syn-variable">{spelled}</code>
            </span>
            <span className="block">
              From:{" "}
              <span className="text-fg">
                {variable?.source ?? "not in the entry's variables"}
              </span>
            </span>
            {filled && (
              <span className="block">
                In this attempt:{" "}
                {variable?.per_round === true ? (
                  <span className="text-fg">
                    {PER_ROUND}; each run's prompt holds it
                  </span>
                ) : value === undefined ? (
                  <span className="text-fg-3">not recorded</span>
                ) : value === "" ? (
                  <span className="text-fg-3 italic">{EMPTY}</span>
                ) : (
                  <span className="font-mono break-words whitespace-pre-wrap text-fg">
                    {value}
                  </span>
                )}
              </span>
            )}
          </span>
        </span>
      )}
    </span>
  );
}
