// Statuses and outcomes: their words and tones, as the project file names them.
import { CircleAlert, CircleCheck, CircleMinus, CircleX } from "lucide-react";

import type { AttemptRecord, Live, Project, Tone } from "../api/types";

// The words the viewer itself gives a status; every other word comes from the
// project file.
/**
 * An attempt with no status whose last visit did not raise, or a visit with
 * no outcome in such an attempt: running, or its process died, which the
 * record cannot tell.
 */
export const NOT_ENDED = "Not ended";
/** A state, a deliverable or the end that the attempt never reached. */
export const NOT_RUN = "Not run";
/** A value a file holds as null: there, with nothing given for it. */
export const NOT_GIVEN = "Not given";
/**
 * An attempt that ended with no status word: its status is null and the
 * project names its own words for not ended (design-viz.md section 4).
 */
const NO_STATUS = "No status";
/** A visit with no outcome in an attempt that has ended. */
const NO_OUTCOME = "No outcome";
/** An attempt whose record cannot be read: the listing's error. */
const UNREADABLE = "Unreadable";
/** A visit a resumption left out, to be run again (design.md). */
const DISCARDED = "Discarded";
/** The engine's outcome of a visit whose state raised (design.md). */
export const RAISED = "raised";

/**
 * A word's label and tone. A word the project file has no entry for is
 * itself, neutral; but raised, the engine's own word for an exception, is
 * bad, and no status (null) is Not ended, neutral.
 */
export function statusOf(
  project: Pick<Project, "statuses">,
  word: string | null,
): { label: string; tone: Tone } {
  if (word === null) return { label: NOT_ENDED, tone: "neutral" };
  const entry = project.statuses[word];
  if (entry) return entry;
  return { label: word, tone: word === RAISED ? "bad" : "neutral" };
}

// The record's status, but null while it is one of the project's not_ended
// words (design-viz.md section 4).
function endStatus(project: Project, record: AttemptRecord): string | null {
  const status = record.status;
  return status !== null && project.not_ended?.includes(status) !== true
    ? status
    : null;
}

/**
 * An attempt's status word: null until it ends, raised when a state did.
 * With the project's not_ended, as the listing's live says, a status among
 * them is null and a null status No status.
 */
export function attemptStatus(
  project: Project,
  record: AttemptRecord,
): string | null {
  const ended = endStatus(project, record);
  if (ended !== null) return ended;
  if (project.not_ended != null)
    return record.status === null ? NO_STATUS : null;
  return record.visits.at(-1)?.outcome === RAISED ? RAISED : null;
}

/**
 * The word at the machine's end: the attempt's status word, null while it has
 * not ended or when a state raised, which reaches no end.
 */
export function endWord(
  project: Project,
  record: AttemptRecord,
): string | null {
  const word = attemptStatus(project, record);
  return word === RAISED ? null : word;
}

/** The label and tone of the viewer's other words for an attempt. */
export const viewerStatus = {
  noStatus: { label: NO_STATUS, tone: "neutral" },
  noOutcome: { label: NO_OUTCOME, tone: "neutral" },
  unreadable: { label: UNREADABLE, tone: "bad" },
  discarded: { label: DISCARDED, tone: "neutral" },
} as const satisfies Record<string, { label: string; tone: Tone }>;

/**
 * A visit's outcome: as statusOf, but No outcome when there is none and its
 * attempt has ended (design-viz.md section 4).
 */
export function outcomeOf(
  project: Pick<Project, "statuses">,
  outcome: string | null,
  ended: boolean,
): { label: string; tone: Tone } {
  return outcome === null && ended
    ? viewerStatus.noOutcome
    : statusOf(project, outcome);
}

/** The label and tone of the listing's live (design-viz.md section 4). */
export const liveStatus: Record<Live, { label: string; tone: Tone }> = {
  running: { label: "Running", tone: "neutral" },
  "may have stopped": { label: "May have stopped", tone: "warn" },
  "not ended": { label: NOT_ENDED, tone: "neutral" },
};

/** A state's title, else its name. */
export function stateTitle(project: Project, state: string): string {
  return project.states[state]?.title ?? state;
}

export const toneText: Record<Tone, string> = {
  good: "text-good",
  bad: "text-bad",
  warn: "text-warn",
  neutral: "text-neutral",
};

/** Each tone's icon: no two tones share a shape (L39). */
export const toneGlyph = {
  good: CircleCheck,
  bad: CircleX,
  warn: CircleAlert,
  neutral: CircleMinus,
};
