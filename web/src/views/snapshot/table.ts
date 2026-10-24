// A step's table: which rows a search and the filters of its columns show,
// the chips of each filter, and the page of rows shown.
import type { Tone } from "../../api/types";
import type { Entries, Row, Snapshot, SnapshotColumn } from "./types";

/** How many rows a page of the table shows. */
const PAGE_ROWS = 50;

/** The key in the address of the filter of a column, by its name. */
export const columnParam = (name: string) => `column-${name}`;

/** A word of a status column, by the snapshot's statuses, else itself. */
export function statusWord(
  statuses: Snapshot["statuses"],
  word: string,
): { label: string; tone: Tone } {
  return statuses?.[word] ?? { label: word, tone: "neutral" };
}

/** Whether a column holds numbers, which the table right-aligns. */
export const isNumeric = (column: SnapshotColumn) =>
  column.format === "number" ||
  column.format === "usd" ||
  column.format === "duration";

interface Chips {
  column: SnapshotColumn;
  /** The rows the search and the other columns' filters leave. */
  all: number;
  /** Each value of the column among those rows, in the order first met. */
  values: { value: string; count: number }[];
}

export interface Filtered {
  shown: Row[];
  hidden: number;
  /** The rows the search alone leaves. */
  searched: number;
  chips: Chips[];
}

const matches = (row: Row, q: string) =>
  q === "" || row.id.toLowerCase().includes(q.toLowerCase());

/**
 * The rows a search of their IDs and the chosen value of each filtered
 * column leave, in the snapshot's order, with the chips of each filter.
 */
export function filterRows(
  entries: Entries,
  q: string,
  chosen: ReadonlyMap<string, string>,
): Filtered {
  const filters = entries.columns.filter((c) => c.filter === true);
  const searched = entries.rows.filter((r) => matches(r, q));
  const passes = (row: Row, skip: string | null) =>
    filters.every(
      (c) =>
        c.name === skip ||
        !chosen.has(c.name) ||
        row.values[c.name] === chosen.get(c.name),
    );
  const shown = searched.filter((r) => passes(r, null));
  const chips = filters.map((column) => {
    const rows = searched.filter((r) => passes(r, column.name));
    const counts = new Map<string, number>();
    for (const row of rows) {
      const value = row.values[column.name];
      if (typeof value === "string")
        counts.set(value, (counts.get(value) ?? 0) + 1);
    }
    return {
      column,
      all: rows.length,
      values: [...counts].map(([value, count]) => ({ value, count })),
    };
  });
  return {
    shown,
    hidden: entries.rows.length - shown.length,
    searched: searched.length,
    chips,
  };
}

/** The table's page number in the address, 1 when it is none. */
export function pageNumber(value: string | null): number {
  const n = Number(value ?? "1");
  return Number.isInteger(n) && n >= 1 ? n : 1;
}

/** The page of rows a page number asks for, held to the pages there are. */
export function pageOf(
  rows: Row[],
  asked: number,
): { rows: Row[]; page: number; pages: number; from: number } {
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_ROWS));
  const page = Math.min(Math.max(1, Math.floor(asked)), pages);
  const from = (page - 1) * PAGE_ROWS;
  return { rows: rows.slice(from, from + PAGE_ROWS), page, pages, from };
}
