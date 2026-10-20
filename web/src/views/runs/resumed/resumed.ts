// Where a record's resumptions fall among its visits, and which discarded
// visit a page names (design-viz.md section 5, "Resumed attempts").
import type { AttemptRecord, Resumption } from "../../../api/types";

/** A resumption, with the number of its first discarded visit on a page. */
export interface Placed {
  resumption: Resumption;
  first: number;
}

/**
 * The record's resumptions by the index of the visit they go before: after
 * their first kept visits, after the last one when kept is past it; those at
 * one place in their order.
 */
export function placeResumptions(record: AttemptRecord): Map<number, Placed[]> {
  const places = new Map<number, Placed[]>();
  let first = 0;
  for (const resumption of record.resumptions ?? []) {
    const at = Math.min(resumption.kept, record.visits.length);
    places.set(at, [...(places.get(at) ?? []), { resumption, first }]);
    first += resumption.discarded.length;
  }
  return places;
}

/** The n-th discarded visit among every resumption's, with its resumption. */
export function discardedAt(record: AttemptRecord, n: number) {
  let rest = n;
  for (const resumption of record.resumptions ?? []) {
    const visit = resumption.discarded[rest];
    if (visit) return { resumption, visit };
    rest -= resumption.discarded.length;
  }
  return undefined;
}

/** What the resumptions carried over, and how many there were. */
export function carried(record: AttemptRecord): { usd: number; count: number } {
  const resumptions = record.resumptions ?? [];
  return {
    usd: resumptions.reduce((sum, r) => sum + r.carried_usd, 0),
    count: resumptions.length,
  };
}
