// How numbers, times and costs read on every page.
import type { Action, AttemptRecord, Column, Read, Visit } from "../api/types";
import { NOT_GIVEN, NOT_RUN } from "./status";

/**
 * The parts of a time in the browser's own time zone, the zone's
 * abbreviation among them as timeZoneName.
 */
function parts(
  date: Date,
  fields: Intl.DateTimeFormatOptions,
  timeZone?: string,
) {
  const found = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    ...fields,
    timeZoneName: "short",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) =>
    found.find((part) => part.type === type)?.value ?? "";
  return get;
}

const numeric: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
};

/**
 * An ISO 8601 time as "2026-03-02 09:15 PST", in the browser's zone or, for
 * a test, timeZone; "—" when it does not parse.
 */
export function time(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const get = parts(
    date,
    {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      ...numeric,
    },
    timeZone,
  );
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} ${get("timeZoneName")}`;
}

/**
 * Seconds as "1h 37m", "35m 56s" or "12s"; with fine, a time under 10s as
 * "340ms" or "1.4s", as a test's is read.
 */
export function duration(seconds: number, fine = false): string {
  if (fine && seconds < 10) {
    const ms = Math.max(0, Math.round(seconds * 1000));
    return ms < 1000 ? `${ms.toString()}ms` : `${(ms / 1000).toFixed(1)}s`;
  }
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

/** A count or a measure as "1,000" or "12.5", at most 2 decimal places. */
export function figure(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

/** US dollars as "$21.42". */
export function usd(value: number): string {
  return `$${value.toFixed(2)}`;
}

/** How a column or a tab field shows its values. */
type Shown = Pick<Column, "format" | "labels">;

/**
 * A value's label among a column's or a tab field's labels: a string's by
 * itself, a boolean's or a number's by its JSON text; undefined for none.
 */
export function labelOf(
  value: unknown,
  labels: Column["labels"],
): string | undefined {
  if (value === null || value === undefined) return undefined;
  return labels?.[typeof value === "string" ? value : JSON.stringify(value)];
}

const isScalar = (v: unknown): boolean =>
  typeof v === "string" || typeof v === "number" || typeof v === "boolean";

/**
 * A value a project's column or tab field reads, as its labels name it
 * (labelOf), else as its format says; "—" when there is none. A list of
 * strings, numbers and booleans shows each item so, joined with ", ", and an
 * empty list is none; a list holding anything else shows as its JSON text.
 */
export function formatValue(value: unknown, shown: Shown): string {
  const { format, labels } = shown;
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    if (value.every(isScalar))
      return value.map((item) => formatValue(item, shown)).join(", ");
  }
  const label = labelOf(value, labels);
  if (label !== undefined) return label;
  if (typeof value === "number") {
    if (format === "usd") return usd(value);
    if (format === "duration") return duration(value);
    return String(value);
  }
  if (typeof value === "string") return format === "time" ? time(value) : value;
  return JSON.stringify(value);
}

/**
 * A value read from a file as it stands: Not run when its file is not
 * there, "—" when it is there without it, Not given for null, else as
 * formatValue shows it.
 */
export function readValue(read: Read, value: unknown, shown: Shown): string {
  if (read === "not run") return NOT_RUN;
  if (read === "missing") return "—";
  return value === null ? NOT_GIVEN : formatValue(value, shown);
}

/** The seconds from one ISO 8601 time to another; null when one is bad. */
export function span(from: string, to: string): number | null {
  const seconds = (Date.parse(to) - Date.parse(from)) / 1000;
  return Number.isNaN(seconds) ? null : seconds;
}

// A visit's start and end: its first action's start and its last's end, else
// its own, which a visit that raised before any action has alone.
const visitStart = (v: Visit): string | undefined =>
  v.actions[0]?.started_at ?? v.started_at ?? undefined;
const visitEnd = (v: Visit): string | undefined =>
  v.actions.at(-1)?.ended_at ?? v.ended_at ?? undefined;

/**
 * When an attempt started and ended, as the home page's listing takes them:
 * the first start of its visits and the last end, a visit's times being its
 * actions', else its own.
 */
export function attemptTimes(record: AttemptRecord): {
  started: string | undefined;
  ended: string | undefined;
} {
  return {
    started: record.visits.map(visitStart).find((t) => t !== undefined),
    ended: record.visits.map(visitEnd).findLast((t) => t !== undefined),
  };
}

/**
 * A visit's seconds, from its start to its end, its actions' or else its
 * own; null when it has neither, or when they do not read.
 */
export function visitSeconds(v: Visit): number | null {
  const from = visitStart(v);
  const to = visitEnd(v);
  return from === undefined || to === undefined ? null : span(from, to);
}

/** What stands for the time of a visit that recorded no action. */
export const NO_ACTION = "no action recorded";

/**
 * A visit's time: its duration (visitSeconds), NO_ACTION when it recorded no
 * action and has no start and end of its own, "—" when its times do not read.
 */
export function visitTime(v: Visit): string {
  const seconds = visitSeconds(v);
  if (seconds !== null) return duration(seconds);
  return v.actions.length === 0 ? NO_ACTION : "—";
}

/** A count of runs: "1 run", "3 runs". */
export const runCount = (n: number): string =>
  `${n.toString()} ${n === 1 ? "run" : "runs"}`;

export const visitCost = (v: Visit): number =>
  v.actions.reduce((sum: number, a: Action) => sum + a.cost_usd, 0);

/** Seconds since the epoch as "2026-03-02 09:15 PST". */
export const epoch = (seconds: number): string =>
  time(new Date(seconds * 1000).toISOString());

/** Seconds since the epoch as "09:15", in the browser's zone: a chart's time axis. */
export function clock(seconds: number): string {
  const get = parts(new Date(seconds * 1000), numeric);
  return `${get("hour")}:${get("minute")}`;
}

/** A day in seconds: a chart's window longer than this dates its ticks. */
export const DAY_S = 86_400;

/**
 * A tick of a chart's time axis whose window lasts span seconds: "09:15"
 * within a day; on a longer window with its day, "03-02 09:15", or "03-02"
 * alone at midnight, in the browser's zone.
 */
export function axisTime(seconds: number, span: number): string {
  if (span <= DAY_S) return clock(seconds);
  const get = parts(new Date(seconds * 1000), {
    month: "2-digit",
    day: "2-digit",
    ...numeric,
  });
  const day = `${get("month")}-${get("day")}`;
  const at = `${get("hour")}:${get("minute")}`;
  return at === "00:00" ? day : `${day} ${at}`;
}

/** Seconds since the epoch as "09:15:42 PST". */
export function instant(seconds: number): string {
  const get = parts(new Date(seconds * 1000), {
    ...numeric,
    second: "2-digit",
  });
  return `${get("hour")}:${get("minute")}:${get("second")} ${get("timeZoneName")}`;
}

/** Seconds since the epoch as "2026-03-02 09:15:42 PST", the day with the time. */
export function moment(seconds: number): string {
  const get = parts(new Date(seconds * 1000), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...numeric,
    second: "2-digit",
  });
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")} ${get("timeZoneName")}`;
}

/** Bytes in binary units, as "512 MiB" or "12.5 GiB". */
export function bytes(value: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = value;
  let unit = 0;
  while (Math.abs(v) >= 1024 && unit < units.length - 1) {
    v /= 1024;
    unit++;
  }
  const digits = unit === 0 || Math.abs(v) >= 100 ? 0 : 1;
  return `${v.toFixed(digits)} ${units[unit] ?? ""}`;
}

/** CPU time per second, as "1.5 cores". */
export const cores = (value: number): string =>
  `${value.toFixed(value >= 10 || Number.isInteger(value) ? 0 : 1)} cores`;

/** A load average, a count of runnable tasks with no unit, as "10.4". */
export const load = (value: number): string =>
  value.toFixed(value >= 100 || Number.isInteger(value) ? 0 : 1);

/** A count with its unit, as "1 run" or "13 runs". */
export const counted = (n: number, one: string, many = `${one}s`): string =>
  `${n.toString()} ${n === 1 ? one : many}`;
