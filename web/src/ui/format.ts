// How numbers, times and costs read on every page.
import type { Action, Visit } from "../api/types";

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

/** Seconds as "1h 37m", "35m 56s" or "12s". */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

/** US dollars as "$21.42". */
export function usd(value: number): string {
  return `$${value.toFixed(2)}`;
}

/** The seconds from one ISO 8601 time to another; null when one is bad. */
export function span(from: string, to: string): number | null {
  const seconds = (Date.parse(to) - Date.parse(from)) / 1000;
  return Number.isNaN(seconds) ? null : seconds;
}

export const visitStart = (v: Visit): string | undefined =>
  v.actions[0]?.started_at;
export const visitEnd = (v: Visit): string | undefined =>
  v.actions.at(-1)?.ended_at;

/** A visit's seconds, from its first action's start to its last's end. */
export function visitSeconds(v: Visit): number | null {
  const from = visitStart(v);
  const to = visitEnd(v);
  return from === undefined || to === undefined ? null : span(from, to);
}

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

/** Seconds since the epoch as "09:15:42 PST". */
export function instant(seconds: number): string {
  const get = parts(new Date(seconds * 1000), {
    ...numeric,
    second: "2-digit",
  });
  return `${get("hour")}:${get("minute")}:${get("second")} ${get("timeZoneName")}`;
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
