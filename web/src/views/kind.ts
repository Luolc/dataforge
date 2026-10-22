import type {
  AttemptRecord,
  Deliverable,
  LiveInfo,
  Project,
} from "../api/types";

/** What a deliverable kind's view gets: one deliverable and its record. */
export interface DeliverableProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  deliverable: Deliverable;
  /** Whether the attempt runs now; null or absent while it is not known. */
  live?: LiveInfo | null;
}

/**
 * Deliverables of a kind in the order their tab stacks them: those the
 * attempt reached, then the others, each in their own order.
 */
export function reachedFirst(items: Deliverable[]): Deliverable[] {
  const reached = (item: Deliverable) => item.path !== null;
  return [...items.filter(reached), ...items.filter((item) => !reached(item))];
}

/**
 * The deliverable of a kind that its tab is titled and counted by and opens
 * on: the first the attempt reached, else the first.
 */
export function leading(items: Deliverable[]): Deliverable | undefined {
  return reachedFirst(items)[0];
}
