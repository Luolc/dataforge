// A job's status: the words and tones of the queue's states, defined here
// alone (L37), and which a job takes; and the lines that say how a job and
// its queue stand where a page waits on one.
import type { Job, Pause, Queued, Tone } from "../../api/types";
import { epoch } from "../../ui/format";
import { NOT_RUN } from "../../ui/status";

const jobStatus = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "neutral" },
  /** Running, with a cancel asked for and not yet ended. */
  canceling: { label: "Canceling", tone: "warn" },
  done: { label: "Done", tone: "good" },
  failed: { label: "Failed", tone: "bad" },
  canceled: { label: "Canceled", tone: "warn" },
} as const satisfies Record<string, { label: string; tone: Tone }>;

const STATES = ["queued", "running", "done", "failed", "canceled"] as const;

/**
 * A job's label and tone: its state's, Canceling once a cancel is asked for;
 * a state this viewer does not know is itself, neutral.
 */
export function jobLabel(job: Job): { label: string; tone: Tone } {
  if (job.state === "running" && job.cancel_requested_at !== null)
    return jobStatus.canceling;
  const state = STATES.find((s) => s === job.state);
  return state === undefined
    ? { label: job.state, tone: "neutral" }
    : jobStatus[state];
}

/** Whether a job holds its key: it is queued or running. */
export const isOpen = (job: Job) =>
  job.state === "queued" || job.state === "running";

/** A job as one short line, as "Canceled (job 98)". */
const jobShort = (job: Job) =>
  `${jobLabel(job).label} (job ${job.id.toString()})`;

const why = (pause: Pause) =>
  pause.reason === null ? "" : `: ${pause.reason}`;

/** A queue's pause beside its name: when and why it was paused. */
export const pausedShort = (pause: Pause) =>
  `Paused since ${epoch(pause.paused_at)}${why(pause)}`;

/** Why no job of a queue starts, as a sentence of its own. */
export const pausedLine = (pause: Pause) =>
  `The ${pause.queue} queue has been paused since ${epoch(pause.paused_at)}${why(pause)}.`;

/**
 * What a column of a tab with no version shows of the tab's job: the job as
 * jobShort gives it, else Not run, and, while its queue is paused, that it
 * is; null when there is neither a job nor a pause.
 */
export function queuedShort(queued: Queued): string | null {
  if (queued.job === null && queued.paused === null) return null;
  const parts = [queued.job === null ? NOT_RUN : jobShort(queued.job)];
  if (queued.paused !== null) parts.push("Queue paused");
  return parts.join(" · ");
}

/** When a job was queued, started and ended, those it has, and its exit code. */
export function jobTimes(job: Job): string {
  const parts = [`queued ${epoch(job.queued_at)}`];
  if (job.started_at !== null) parts.push(`started ${epoch(job.started_at)}`);
  if (job.ended_at !== null) parts.push(`ended ${epoch(job.ended_at)}`);
  if (job.exit_code !== null)
    parts.push(`exit code ${job.exit_code.toString()}`);
  return parts.join(" · ");
}
