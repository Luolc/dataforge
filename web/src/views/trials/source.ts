// The trial whose output a trial judged again, as the record lists it.
import {
  type AttemptRecord,
  type Deliverable,
  deliverablesOf,
  type Trial,
} from "../../api/types";

/** A trial, the deliverable of trials that lists it and where in its list. */
export interface Listed {
  deliverable: Deliverable;
  trial: Trial;
  index: number;
}

/**
 * The trial at a trial's source, in the first deliverable of trials of the
 * record that lists one at that path; null when none does.
 */
export function sourceOf(record: AttemptRecord, source: string): Listed | null {
  for (const deliverable of deliverablesOf(record)) {
    if (deliverable.kind !== "trials") continue;
    const trials = deliverable.trials ?? [];
    const index = trials.findIndex((t) => t.path === source);
    const trial = trials[index];
    if (trial !== undefined) return { deliverable, trial, index };
  }
  return null;
}

/**
 * A trial's name, by its place in its deliverable's list from 1, which
 * lists its trials in the order of their outputs.
 */
export function trialName(index: number): string {
  return `Trial ${String(index + 1)}`;
}
