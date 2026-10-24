// What the home page's table shares with every other list of items drawn the
// same way, the share site's among them: a project's columns, each with its
// value and its cell, and a row that opens its item wherever it is clicked.
// Their headings and filters are in ColumnControls.tsx.
import type { MouseEvent, ReactNode } from "react";
import { useNavigate } from "react-router";

import type { Column, Queued } from "../../api/types";
import { formatValue, labelOf, readValue } from "../../ui/format";
import { Hint } from "../../ui/hint";
import { queuedShort } from "../queue/status";

export type Value = number | string | undefined;

/** A column: its heading, the value it sorts by and its cell. */
export interface Spec<L> {
  id: string;
  title: string;
  numeric: boolean;
  /** What the column holds, on its heading's hover and focus. */
  description?: string | null;
  value: (line: L) => Value;
  cell: (line: L) => ReactNode;
}

/** A row's value in a column, how it was read, and the job that makes it. */
export interface Cell {
  value: unknown;
  /** Null for a value of the record; else how its file was read. */
  read: "found" | "not run" | "missing" | null;
  /** The job of the column's tab, while the tab has no version. */
  queued: Queued | null;
}

/** Two values of one column, all numbers or all strings. */
export function compareValues(x: Value, y: Value): number {
  if (typeof x === "number" && typeof y === "number") return x - y;
  return String(x).localeCompare(String(y), undefined, { numeric: true });
}

export function timeValue(iso: string | null): Value {
  const t = iso === null ? NaN : Date.parse(iso);
  return Number.isNaN(t) ? undefined : t;
}

const NUMERIC_FORMATS = new Set(["number", "usd", "duration", "time"]);

/** The key in the URL of the filter of the column at index. */
export const columnParam = (index: number) => `column-${index.toString()}`;

/** Dotted, as a hint's sign. */
export const DOTTED =
  "underline decoration-faint decoration-dotted underline-offset-3";

/** The class of a row that opens its item, under a hovering pointer too. */
export const ROW_CLASS =
  "group/row cursor-pointer border-b border-line-subtle hover:bg-hover";

/**
 * A row's click: it opens href, but not when it lands on a control in the
 * row, which the keyboard reaches on its own.
 */
export function useOpenRow(): (event: MouseEvent, href: string) => void {
  const navigate = useNavigate();
  return (event, href) => {
    if (
      event.target instanceof Element &&
      event.target.closest("a, button, [tabindex]")
    )
      return;
    void navigate(href);
  };
}

/** The spec of the project's column at index, its cell as at gives it. */
export function columnSpec<L>(
  column: Column,
  index: number,
  at: (line: L) => Cell,
): Spec<L> {
  return {
    id: columnParam(index),
    title: column.title,
    description: column.description ?? null,
    numeric: NUMERIC_FORMATS.has(column.format),
    value: (l) => {
      const v = at(l).value;
      if (column.format === "time" && typeof v === "string")
        return timeValue(v);
      // A value with a label sorts by it, as it shows.
      const label = labelOf(v, column.labels);
      if (label !== undefined) return label;
      if (Array.isArray(v) && v.length > 0) return formatValue(v, column);
      return typeof v === "number" || typeof v === "string" ? v : undefined;
    },
    cell: (l) => {
      const { value: v, read, queued: job } = at(l);
      // A tab not run yet says how its job stands, when it names one.
      const queued = read === null || job === null ? null : queuedShort(job);
      const given =
        read === null
          ? v !== null && v !== undefined
          : read === "found" && v !== null;
      if (given) {
        const shown = queued ?? formatValue(v, column);
        const hint = labelOf(v, column.hints);
        return hint === undefined ? (
          shown
        ) : (
          <Hint hint={hint}>
            <span className={DOTTED}>{shown}</span>
          </Hint>
        );
      }
      // A column of cases is empty where none of them holds, unless it
      // says what that stands for.
      if ((column.cases ?? []).length > 0 && column.none == null) return null;
      if (read === null && column.none == null) return "—";
      // A value it has not read, or null, is a word of the viewer's, kept
      // whole, or "—" when the column says what no value stands for; null,
      // or that, says on hover what it stands for.
      const hint =
        column.none ??
        (read === "found" ? "Its file gives null for this value." : null);
      const word = (
        <span
          className={`whitespace-nowrap text-fg-3 ${hint === null ? "" : DOTTED}`}
        >
          {queued ??
            (column.none != null || read === null
              ? "—"
              : readValue(read, v, column))}
        </span>
      );
      return hint === null ? word : <Hint hint={hint}>{word}</Hint>;
    },
  };
}
