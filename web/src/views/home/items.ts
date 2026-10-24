// The home page's model: attempt rows grouped into data items, the items
// split into the page's groups, and what search and the filter's rules keep.
import type { AttemptRow, Column, Live, Project } from "../../api/types";
import {
  isEnding,
  liveStatus,
  NOT_ENDED,
  statusOf,
  viewerStatus,
} from "../../ui/status";
import type { Rule } from "../../ui/rule";
import { columnKey, STATUS_FIELD } from "./view";

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
export const UNREADABLE_KEY = "~unreadable";
/** A status word for an attempt that has ended with no status word. */
export const NO_STATUS_KEY = "~no-status";
/** A status word for an attempt that may have stopped and has. */
const STOPPED_KEY = "~stopped";

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
 * An attempt's status word: UNREADABLE_KEY when its record cannot be read; the
 * status of its row when that is interrupted, the word for how it was stopped;
 * STOPPED_KEY when it may have stopped and the job that ran it has ended;
 * while it has not ended, live's word (null for not ended); else its record's
 * status, its last visit's outcome when that raised or was interrupted, or
 * NO_STATUS_KEY.
 */
export function rowStatus(row: AttemptRow): string | null {
  if (row.error !== null) return UNREADABLE_KEY;
  if (row.state === "interrupted" && row.status !== null) return row.status;
  if (row.live === "may have stopped" && row.stopped != null)
    return STOPPED_KEY;
  if (row.live !== null && row.live !== undefined) return LIVE_WORD[row.live];
  if (row.status !== null) return row.status;
  const last = row.rounds?.at(-1)?.outcome;
  return last !== undefined && isEnding(last) ? last : NO_STATUS_KEY;
}

/**
 * Whether an attempt is empty, which the page hides unless asked and labels
 * an older attempt: its record cannot be read, or it has no status and no
 * visit, and it is not live and not the one its item's held lock is for, so
 * where the project has a lock, an attempt that has just started and has not
 * written its record yet stays shown, and its item's older ones do not.
 */
export const isEmptyAttempt = (row: AttemptRow): boolean =>
  row.holder !== true &&
  (row.live ?? null) === null &&
  (row.error !== null ||
    (row.status === null && (row.rounds ?? []).length === 0));

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

/** n and a noun, the noun with an s unless n is 1: "3 CVEs", "1 attempt". */
export const counted = (n: number, noun: string): string =>
  `${n.toString()} ${noun}${n === 1 ? "" : "s"}`;

type Round = NonNullable<AttemptRow["rounds"]>[number];

/** A run of rounds in a row that ended alike: the same state and outcome. */
export interface RoundRun {
  first: number;
  last: number;
  count: number;
  state: string | null;
  outcome: string | null;
}

/**
 * The rounds, each run of them in a row with the same state and outcome as
 * one; with open, the last round, which runs now, stands alone.
 */
export function roundRuns(rounds: Round[], open: boolean): RoundRun[] {
  const runs: RoundRun[] = [];
  rounds.forEach((r, i) => {
    const before = runs.at(-1);
    const alone = open && i === rounds.length - 1;
    if (
      before !== undefined &&
      !alone &&
      before.state === r.state &&
      before.outcome === r.outcome
    ) {
      before.last = r.round;
      before.count += 1;
    } else
      runs.push({
        first: r.round,
        last: r.round,
        count: 1,
        state: r.state,
        outcome: r.outcome,
      });
  });
  return runs;
}

/** A time as milliseconds for ordering; one missing or bad is the oldest. */
function ms(iso: string | null): number {
  const t = iso === null ? NaN : Date.parse(iso);
  return Number.isNaN(t) ? -Infinity : t;
}

/**
 * When an attempt started, for finding an item's newest: one that has not
 * ended and has no started_at has not finished its first action yet, so it
 * is newer than any that has. An ended one with no started_at, such as one
 * that failed before its first action, is the oldest.
 */
const startMs = (row: AttemptRow): number =>
  row.started_at === null && notEnded(row) ? Infinity : ms(row.started_at);

const newestFirst = (a: number, b: number) => (a === b ? 0 : a > b ? -1 : 1);

/** The rows, newest started first, as an item's attempts are ordered. */
export const byNewestStart = (rows: AttemptRow[]): AttemptRow[] =>
  [...rows].sort((a, b) => newestFirst(startMs(a), startMs(b)));

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
    const attempts = byNewestStart(list);
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
  return items.sort((a, b) => {
    const byGroup = rank(a.group) - rank(b.group);
    if (byGroup !== 0) return byGroup;
    return a.group === "unreadable"
      ? a.id.localeCompare(b.id)
      : newestFirst(time(a), time(b));
  });
}

/** The key of an item's status chip. */
function filterKey(item: Item): string {
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
      [STOPPED_KEY]: viewerStatus.stopped,
      [NO_STATUS_KEY]: viewerStatus.noStatus,
      [UNREADABLE_KEY]: viewerStatus.unreadable,
    },
  };
}

/** The status word a chip's key stands for. */
export const keyWord = (key: string): string | null =>
  key === NOT_ENDED_KEY ? null : key;

/** Whether an item's ID holds the search text, ignoring case. */
function matches(item: Item, q: string): boolean {
  return item.id.toLowerCase().includes(q.trim().toLowerCase());
}

/**
 * The key of a value of a column a page filters by: a string as it is, a
 * number's or a boolean's JSON text, as a label is keyed; null for none.
 */
function valueKey(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return JSON.stringify(value);
  return null;
}

/** An item's value in the column at index, its newest's. */
const columnValue = (item: Item, index: number): unknown =>
  item.newest.values?.[index];

/** A chip's key and the number of items it has. */
interface ChipCount {
  key: string;
  count: number;
}

/** The chips of a column the page filters by. */
export interface ColumnChips {
  /** The column's index among the project's columns. */
  index: number;
  /** The items the search and every other filter keep, its All chip's count. */
  all: number;
  /** One per value those items have, in the order of the column's cases, then A to Z. */
  chips: ChipCount[];
}

/** The value of a row that has none in a field, in a rule and the URL. */
export const NO_VALUE = "~none";

/** A field a list filters by: its key, a row's value in it, its values' order. */
export interface Field<T> {
  key: string;
  valueOf: (row: T) => string;
  order: (a: string, b: string) => number;
}

/** A field's values over the rows the search and the other fields' rules keep. */
export interface FieldCounts {
  field: string;
  /** How many rows those are. */
  all: number;
  /** One per value they have, and one per value its rule names, in the field's order. */
  values: ChipCount[];
}

export interface Filtered {
  /** The items the search and the rules keep, in the page's order. */
  shown: Item[];
  /** Each field's values, in the fields' order, the status field first. */
  fields: FieldCounts[];
  /** How many items the search and the rules hide. */
  hidden: number;
}

// The chips of the live words come first, unreadable last.
const FIRST = ["~running", "~may-have-stopped", STOPPED_KEY, NOT_ENDED_KEY];

/** Each key's count over the items, a chosen key at 0 when none has it. */
function countKeys<T>(
  items: T[],
  key: (item: T) => string | null,
  chosen: readonly string[],
): Map<string, number> {
  const counts = new Map<string, number>(chosen.map((c) => [c, 0]));
  for (const item of items) {
    const k = key(item);
    if (k !== null) counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  return counts;
}

/** The status field: an item's status word, live words first, unreadable last. */
function statusField(project: Project): Field<Item> {
  const declared = Object.keys(project.statuses);
  const order = (key: string) =>
    FIRST.includes(key)
      ? FIRST.indexOf(key) - FIRST.length
      : key === UNREADABLE_KEY
        ? Infinity
        : declared.includes(key)
          ? declared.indexOf(key)
          : declared.length;
  return {
    key: STATUS_FIELD,
    valueOf: filterKey,
    order: (a, b) => {
      const byOrder = order(a) - order(b);
      // Two unreadable keys give Infinity - Infinity, NaN; they are the
      // same key, so the name comparison settles them.
      return byOrder !== 0 && !Number.isNaN(byOrder)
        ? byOrder
        : a.localeCompare(b);
    },
  };
}

/**
 * A column's values in the order of its cases, then by label A to Z, and
 * no value last.
 */
function columnOrder(column: Column): (a: string, b: string) => number {
  const cases = column.cases ?? [];
  const rank = (key: string) =>
    key === NO_VALUE
      ? cases.length + 1
      : cases.includes(key)
        ? cases.indexOf(key)
        : cases.length;
  const label = (key: string) => column.labels?.[key] ?? key;
  return (a, b) => {
    const byRank = rank(a) - rank(b);
    return byRank !== 0 ? byRank : label(a).localeCompare(label(b));
  };
}

/**
 * The fields the home page filters by: the status, then each column with
 * filter, by its key (columnKey), the first of two with one title.
 */
export function homeFields(project: Project): Field<Item>[] {
  const fields = [statusField(project)];
  project.columns.forEach((column, index) => {
    const key = columnKey(column.title);
    if (column.filter !== true || fields.some((f) => f.key === key)) return;
    fields.push({
      key,
      valueOf: (i) => valueKey(columnValue(i, index)) ?? NO_VALUE,
      order: columnOrder(column),
    });
  });
  return fields;
}

/**
 * Whether a row keeps to every rule but that of the field but: its value in
 * the rule's field is one of the rule's values (any) or none of them (none).
 * A rule of a field not among fields keeps every row.
 */
export function byRules<T>(
  row: T,
  fields: readonly Field<T>[],
  rules: readonly Rule[],
  but?: string,
): boolean {
  return rules.every((rule) => {
    const field = fields.find((f) => f.key === rule.field);
    if (rule.field === but || field === undefined) return true;
    return rule.values.includes(field.valueOf(row)) === (rule.op === "any");
  });
}

/**
 * Each field's values over the rows the other fields' rules keep, so a
 * value's count says what choosing it would show; a value a rule names stays,
 * at 0 when no such row has it.
 */
export function fieldCounts<T>(
  fields: readonly Field<T>[],
  rows: T[],
  rules: readonly Rule[],
): FieldCounts[] {
  return fields.map((field) => {
    const kept = rows.filter((r) => byRules(r, fields, rules, field.key));
    const chosen = rules.find((r) => r.field === field.key)?.values ?? [];
    return {
      field: field.key,
      all: kept.length,
      values: [...countKeys(kept, field.valueOf, chosen)]
        .map(([key, count]) => ({ key, count }))
        .sort((a, b) => field.order(a.key, b.key)),
    };
  });
}

/**
 * What a search and the rules keep: the items whose ID holds the search
 * and that keep to every rule. Each field counts the items the search and
 * the other fields' rules keep.
 */
export function filterItems(
  project: Project,
  items: Item[],
  q: string,
  rules: readonly Rule[],
  fields: readonly Field<Item>[] = homeFields(project),
): Filtered {
  const searched = items.filter((i) => matches(i, q));
  const shown = searched.filter((i) => byRules(i, fields, rules));
  return {
    shown,
    fields: fieldCounts(fields, searched, rules),
    hidden: items.length - shown.length,
  };
}

/**
 * Whether a row has the value picked in each column of picked (by the
 * column's index) but the column at but.
 */
export function byColumns<T>(
  row: T,
  valueOf: (row: T, index: number) => unknown,
  picked: ReadonlyMap<number, string>,
  but?: number,
): boolean {
  return [...picked].every(
    ([index, key]) => index === but || valueKey(valueOf(row, index)) === key,
  );
}

/**
 * The chips of each column that filters, over the rows the search and the
 * other filters keep: each column's counts the rows the other columns'
 * picks keep, in the order of the column's cases, then by label A to Z.
 */
export function columnChips<T>(
  columns: Column[],
  rows: T[],
  valueOf: (row: T, index: number) => unknown,
  picked: ReadonlyMap<number, string>,
): ColumnChips[] {
  return columns.flatMap((column, index): ColumnChips[] => {
    if (column.filter !== true) return [];
    const kept = rows.filter((r) => byColumns(r, valueOf, picked, index));
    const pick = picked.get(index);
    const counted = countKeys(
      kept,
      (r) => valueKey(valueOf(r, index)),
      pick === undefined ? [] : [pick],
    );
    const order = columnOrder(column);
    return [
      {
        index,
        all: kept.length,
        chips: [...counted]
          .map(([key, count]) => ({ key, count }))
          .sort((a, b) => order(a.key, b.key)),
      },
    ];
  });
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
