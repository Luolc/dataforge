// The home page's model: attempt rows grouped into data items, the items
// split into the page's groups, and what search and the status chips keep.
import type { AttemptRow, Live, Project } from "../../api/types";
import { liveStatus, NOT_ENDED, RAISED, statusOf } from "../../ui/status";

/**
 * Each live word as a status word of the home page; not ended is null, the
 * word of a record with no status, so the two share the Not ended chip.
 */
const LIVE_WORD: Record<Live, string | null> = {
  running: "~running",
  "may have stopped": "~may-have-stopped",
  "not ended": null,
};

/** A filter key for items whose newest attempt has not ended. */
export const NOT_ENDED_KEY = "~not-ended";
/** A filter key, and a status word, for items whose record cannot be read. */
export const UNREADABLE = "~unreadable";
/** A status word for an attempt that has ended with no status word. */
export const NO_STATUS = "~no-status";

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
 * Whether an attempt has not ended: the listing's live is not null
 * (design-viz.md section 5, "Home page").
 */
export const notEnded = (row: AttemptRow): boolean =>
  row.error === null && (row.live ?? null) !== null;

/**
 * An attempt's status word: UNREADABLE when its record cannot be read; while
 * it has not ended, live's word (null for not ended); else its record's
 * status, raised when its last visit raised, or NO_STATUS.
 */
export function rowStatus(row: AttemptRow): string | null {
  if (row.error !== null) return UNREADABLE;
  if (row.live !== null && row.live !== undefined) return LIVE_WORD[row.live];
  if (row.status !== null) return row.status;
  return row.rounds?.at(-1)?.outcome === RAISED ? RAISED : NO_STATUS;
}

/** An attempt's seconds from start to end; null until it ends. */
export function rowSeconds(row: AttemptRow): number | null {
  if (notEnded(row) || row.started_at === null || row.ended_at === null)
    return null;
  const seconds =
    (Date.parse(row.ended_at) - Date.parse(row.started_at)) / 1000;
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

/**
 * When an attempt started, for finding an item's newest: a readable record
 * with no started_at has not finished its first action yet, so it is newer
 * than any that has.
 */
const startMs = (row: AttemptRow): number =>
  row.error === null && row.started_at === null ? Infinity : ms(row.started_at);

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
      newestFirst(startMs(a), startMs(b)),
    );
    const newest = attempts[0];
    if (newest === undefined) continue;
    const group: Group =
      newest.error !== null
        ? "unreadable"
        : notEnded(newest)
          ? "open"
          : "ended";
    items.push({ id, attempts, newest, group });
  }
  const rank = (g: Group) => GROUPS.findIndex((x) => x.group === g);
  const time = (i: Item) =>
    i.group === "open" ? startMs(i.newest) : ms(i.newest.ended_at);
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
 * The project with the home page's own status words, so the shared status
 * labels draw them like any other.
 */
export function withHomeStatuses(project: Project): Project {
  return {
    ...project,
    statuses: {
      ...project.statuses,
      "~running": liveStatus.running,
      "~may-have-stopped": liveStatus["may have stopped"],
      [NO_STATUS]: { label: "No status", tone: "neutral" },
      [UNREADABLE]: { label: "Unreadable", tone: "bad" },
    },
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
  /** The items search and the chosen chip keep, in the page's order. */
  shown: Item[];
  /** Each chip's key and the number of searched items it has. */
  chips: { key: string; count: number }[];
  /** The number of searched items, the All chip's count. */
  searched: number;
  /** How many items search and the chip hide. */
  hidden: number;
}

// The chips of the live words come first, unreadable last.
const FIRST = ["~running", "~may-have-stopped", NOT_ENDED_KEY];

/**
 * What a search and a chosen chip keep; no chip chosen (All) keeps every
 * status. Chips count the items search keeps, so a chip says what choosing
 * it would show; the chosen chip stays when search leaves it none.
 */
export function filterItems(
  project: Project,
  items: Item[],
  q: string,
  chosen: string | null,
): Filtered {
  const searched = items.filter((i) => matches(i, q));
  const counts = new Map<string, number>(chosen === null ? [] : [[chosen, 0]]);
  for (const item of searched) {
    const key = filterKey(item);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const declared = Object.keys(project.statuses);
  const order = (key: string) =>
    FIRST.includes(key)
      ? FIRST.indexOf(key) - FIRST.length
      : key === UNREADABLE
        ? Infinity
        : declared.includes(key)
          ? declared.indexOf(key)
          : declared.length;
  const chips = [...counts]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => order(a.key) - order(b.key) || a.key.localeCompare(b.key));
  const shown =
    chosen === null
      ? searched
      : searched.filter((i) => filterKey(i) === chosen);
  return {
    shown,
    chips,
    searched: searched.length,
    hidden: items.length - shown.length,
  };
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
