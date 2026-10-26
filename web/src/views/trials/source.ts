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
 * Each trial's name, by its slot: Trial n for the n-th trial of the list,
 * from 1, that takes no other's place, which the engine lists in the order
 * of their outputs; a trial that took one's place has the slot of the trial
 * it replaced. A slot with more than one trial adds run m to each, from 1,
 * in the order they took its place.
 */
export function trialNames(
  trials: Pick<Trial, "output" | "replaces">[],
): string[] {
  const replaced = new Map(
    trials.flatMap((t) => (t.replaces == null ? [] : [[t.output, t.replaces]])),
  );
  const slots = new Map<string, number>();
  const runs = new Map<string, number>();
  const placed = trials.map((trial) => {
    let first = trial.output;
    let run = 1;
    // A chain no longer than the list: a record that loops ends there.
    while (replaced.has(first) && run <= trials.length) {
      first = replaced.get(first) ?? first;
      run += 1;
    }
    if (!slots.has(first)) slots.set(first, slots.size + 1);
    runs.set(first, Math.max(runs.get(first) ?? 0, run));
    return { first, run };
  });
  return placed.map(({ first, run }) => {
    const name = `Trial ${String(slots.get(first))}`;
    return (runs.get(first) ?? 1) > 1 ? `${name}, run ${String(run)}` : name;
  });
}

/** The name trialNames gives the trial at index of trials. */
export function trialName(
  trials: Pick<Trial, "output" | "replaces">[],
  index: number,
): string {
  return trialNames(trials)[index] ?? `Trial ${String(index + 1)}`;
}
