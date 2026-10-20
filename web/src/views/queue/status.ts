// A job's status: the words and tones of the queue's states, defined here
// alone (L37), and which a job takes.
import type { Job, Tone } from "../../api/types";

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
