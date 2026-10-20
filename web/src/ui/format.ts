// How numbers, times and costs read on every page.
import type { Action, Visit } from "../api/types";

const utc = new Intl.DateTimeFormat("en-CA", {
  timeZone: "UTC",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** An ISO 8601 time as "2026-03-02 09:15 UTC"; "—" when it does not parse. */
export function time(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${utc.format(date).replace(",", "")} UTC`;
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
