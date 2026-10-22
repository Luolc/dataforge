// Where a deliverable came from: the run that made it, and, for one that
// judged or reviewed a files deliverable, whether a later run made the files
// the attempt delivers.
import type { AttemptRecord, Deliverable } from "../api/types";

export type Placed = Pick<
  Deliverable,
  "kind" | "state" | "visit_index" | "path"
>;

/** A reached deliverable's title, state and run. */
export interface Made {
  title: string;
  state: string;
  /** The run's place in the record's visits. */
  index: number;
}

/**
 * The files deliverable the attempt reached from a run after this one's, the
 * first in declaration order: what this one ran before. Null for a files
 * deliverable, one not reached, or when none is later.
 */
export function laterFiles(
  record: AttemptRecord,
  deliverable: Placed,
): Made | null {
  const at = deliverable.visit_index;
  if (deliverable.kind === "files" || deliverable.path === null || at === null)
    return null;
  for (const d of record.deliverables ?? []) {
    const index = d.visit_index;
    if (d.kind === "files" && d.path !== null && index !== null && index > at)
      return { title: d.title, state: d.state, index };
  }
  return null;
}
