// The home page's model: attempt rows grouped into data items, the items
// split into the page's groups, and what search and the status chips keep.
import type { AttemptRow, Project } from "../../api/types";
import { NOT_ENDED, RAISED, statusOf } from "../../ui/status";

/** A filter key for items whose newest attempt has not ended. */
export const NOT_ENDED_KEY = "~not-ended";
/** A filter key, and a status word, for items whose record cannot be read. */
export const UNREADABLE = "~unreadable";

export type Group = "open" | "ended" | "unreadable";

/** The page's groups, in the order they are shown, with their headings. */
export const GROUPS: { group: Group; title: string }[] = [
  { group: "open", title: NOT_ENDED },
  { group: "ended", title: "Ended" },
  { group: "unreadable", title: "Unreadable record" },
];

export interface Item {
  id: string;
  /** Its attempts, newest started_at first. */
  attempts: AttemptRow[];
  newest: AttemptRow;
  group: Group;
}

/**
 * An attempt's status word: its record's status, else raised when its last
 * visit raised, else null while it has not ended. UNREADABLE when its record
 * cannot be read.
 */
export function rowStatus(row: AttemptRow): string | null {
  if (row.error !== null) return UNREADABLE;
  if (row.status !== null) return row.status;
  return row.rounds?.at(-1)?.outcome === RAISED ? RAISED : null;
}

/**
 * Whether an attempt has not ended: its record says no status and its last
 * visit did not raise (design-viz.md section 5, "Home page").
 */
export const notEnded = (row: AttemptRow): boolean => rowStatus(row) === null;

/** An attempt's seconds from start to end; null until it ends. */
export function rowSeconds(row: AttemptRow): number | null {
  if (notEnded(row) || row.started_at === null || row.ended_at === null)
    return null;
  const seconds = (Date.parse(row.ended_at) - Date.parse(row.started_at)) / 1000;
  return Number.isNaN(seconds) ? null : seconds;
}

/** The sum of the attempts' costs; null when none has one. */
export function spent(rows: AttemptRow[]): number | null {
  const costs = rows.flatMap((r) => (r.cost_usd === null ? [] : [r.cost_usd]));
  return costs.length === 0 ? null : costs.reduce((a, b) => a + b, 0);
}

/** A time as milliseconds for ordering; one missing or bad is the oldest. */
function ms(iso: string | null): number {
  const t = iso === null ? NaN : Date.parse(iso);
  return Number.isNaN(t) ? -Infinity : t;
}

const newestFirst = (a: number, b: number) => (a === b ? 0 : a > b ? -1 : 1);

/**
 * The rows grouped by item, in the page's order: items not ended, newest
 * started first; ended ones, newest ended first; unreadable ones by ID.
 */
export function toItems(rows: AttemptRow[]): Item[] {
  const byItem = new Map<string, AttemptRow[]>();
  for (const row of rows) {
    const list = byItem.get(row.item);
    if (list) list.push(row);
    else byItem.set(row.item, [row]);
  }
  const items: Item[] = [];
  for (const [id, list] of byItem) {
    const attempts = [...list].sort((a, b) =>
      newestFirst(ms(a.started_at), ms(b.started_at)),
    );
    const newest = attempts[0];
    if (newest === undefined) continue;
    const word = rowStatus(newest);
    const group: Group =
      word === UNREADABLE ? "unreadable" : word === null ? "open" : "ended";
    items.push({ id, attempts, newest, group });
  }
  const rank = (g: Group) => GROUPS.findIndex((x) => x.group === g);
  const time = (i: Item) =>
    ms(i.group === "open" ? i.newest.started_at : i.newest.ended_at);
  return items.sort(
    (a, b) =>
      rank(a.group) - rank(b.group) ||
      (a.group === "unreadable"
        ? a.id.localeCompare(b.id)
        : newestFirst(time(a), time(b))),
  );
}

/** The key of an item's status chip. */
export function filterKey(item: Item): string {
  return rowStatus(item.newest) ?? NOT_ENDED_KEY;
}

/**
 * The project with the home page's own status word, UNREADABLE, as a bad
 * Unreadable, so the shared status labels draw it like any other.
 */
export function withHomeStatuses(project: Project): Project {
  const unreadable = { label: "Unreadable", tone: "bad" as const };
  return {
    ...project,
    statuses: { ...project.statuses, [UNREADABLE]: unreadable },
  };
}

/** The status word a chip's key stands for. */
export const keyWord = (key: string): string | null =>
  key === NOT_ENDED_KEY ? null : key;

/** Whether an item's ID holds the search text, ignoring case. */
export function matches(item: Item, q: string): boolean {
  return item.id.toLowerCase().includes(q.trim().toLowerCase());
}

export interface Filtered {
  /** The items search and the chips keep, in the page's order. */
  shown: Item[];
  /** Each chip's key and the number of searched items it has. */
  chips: { key: string; count: number }[];
  /** How many items search and the chips hide. */
  hidden: number;
}

/**
 * What a search and a set of chosen chips keep; no chip chosen keeps every
 * status. Chips count the items search keeps, so a chip says what choosing
 * it would show; a chosen chip stays when search leaves it none.
 */
export function filterItems(
  project: Project,
  items: Item[],
  q: string,
  chosen: string[],
): Filtered {
  const searched = items.filter((i) => matches(i, q));
  const counts = new Map<string, number>(chosen.map((k) => [k, 0]));
  for (const item of searched) {
    const key = filterKey(item);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const declared = Object.keys(project.statuses);
  const order = (key: string) =>
    key === NOT_ENDED_KEY
      ? -2
      : key === UNREADABLE
        ? Infinity
        : declared.includes(key)
          ? declared.indexOf(key)
          : declared.length;
  const chips = [...counts]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => order(a.key) - order(b.key) || a.key.localeCompare(b.key));
  const shown =
    chosen.length === 0
      ? searched
      : searched.filter((i) => chosen.includes(filterKey(i)));
  return { shown, chips, hidden: items.length - shown.length };
}

/**
 * How many of the items have a status of tone bad or warn (L44), with
 * project's statuses as withHomeStatuses gives them.
 */
export function flagged(project: Project, items: Item[]): number {
  return items.filter((i) => {
    const { tone } = statusOf(project, rowStatus(i.newest));
    return tone === "bad" || tone === "warn";
  }).length;
}
