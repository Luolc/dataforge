// Statuses and outcomes: their words and tones, as the project file names them.
import { CircleAlert, CircleCheck, CircleMinus, CircleX } from "lucide-react";

import type {
  AttemptRecord,
  Live,
  LiveInfo,
  Project,
  Tone,
} from "../api/types";

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
const RAISED = "raised";
/** The engine's outcome of a visit an interrupt ended, such as SIGTERM. */
const INTERRUPTED = "interrupted";

/**
 * The engine's own outcomes that end an attempt with no transition, and how
 * each reads: always as a visit's outcome, and as an attempt's status when
 * the project file has no entry for it.
 */
const ENGINE_WORDS: Partial<Record<string, { label: string; tone: Tone }>> = {
  [RAISED]: { label: "Exception", tone: "bad" },
  [INTERRUPTED]: { label: "Run interrupted", tone: "warn" },
};

/** Whether a visit's outcome is one the engine ends an attempt with. */
export const isEnding = (outcome: string | null | undefined): boolean =>
  outcome === RAISED || outcome === INTERRUPTED;

/**
 * A name the project file gives no words for, as a person reads it: its
 * underscores as spaces and its first letter a capital (`unit_tests` reads
 * `Unit tests`), so that no page shows a name as the machine writes it.
 */
function readable(name: string): string {
  const spaced = name.replace(/_+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * A status word's label and tone. A word the project file has no entry for
 * reads as `readable` spells it, neutral; but the engine's own words, raised
 * and interrupted, read as Exception, bad, and Run interrupted, warn; and no
 * status (null) is Not ended, neutral.
 */
export function statusOf(
  project: Pick<Project, "statuses">,
  word: string | null,
): { label: string; tone: Tone } {
  if (word === null) return { label: NOT_ENDED, tone: "neutral" };
  return (
    project.statuses[word] ??
    ENGINE_WORDS[word] ?? { label: readable(word), tone: "neutral" }
  );
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
 * The record as a page shows it: with the project's runs and a row of the
 * attempt in the store, as /live gives it, its status and reason are the row's
 * (the status null while it runs), not the record file's own (design-viz.md
 * section 4).
 */
export function withRowStatus(
  project: Project,
  record: AttemptRecord,
  live: LiveInfo | null,
): AttemptRecord {
  if (project.runs == null || live?.state == null) return record;
  return {
    ...record,
    status: live.status ?? null,
    reason: live.reason ?? null,
  };
}

/**
 * An attempt's status word: null until it ends, the last visit's outcome
 * when a state raised or was interrupted.
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
  const last = record.visits.at(-1)?.outcome;
  return last !== undefined && isEnding(last) ? last : null;
}

/**
 * The word at the machine's end: the attempt's status word, null while it has
 * not ended or when a state raised or was interrupted, which reaches no end.
 */
export function endWord(
  project: Project,
  record: AttemptRecord,
): string | null {
  const word = attemptStatus(project, record);
  return isEnding(word) ? null : word;
}

/** The label and tone of the viewer's other words for an attempt. */
export const viewerStatus = {
  noStatus: { label: NO_STATUS, tone: "neutral" },
  noOutcome: { label: NO_OUTCOME, tone: "neutral" },
  unreadable: { label: UNREADABLE, tone: "bad" },
  discarded: { label: DISCARDED, tone: "neutral" },
  /**
   * An attempt that may have stopped, whose item's lock is free and whose
   * job has ended: nothing runs it.
   */
  stopped: { label: "Stopped", tone: "warn" },
} as const satisfies Record<string, { label: string; tone: Tone }>;

/**
 * A visit's outcome: as statusOf, but No outcome when there is none and its
 * attempt has ended, and the engine's own words as the viewer reads them
 * whatever the project file says, as its entries are for attempts' statuses
 * (design-viz.md section 4).
 */
export function outcomeOf(
  project: Pick<Project, "statuses">,
  outcome: string | null,
  ended: boolean,
): { label: string; tone: Tone } {
  if (outcome === null && ended) return viewerStatus.noOutcome;
  return (
    (outcome !== null ? ENGINE_WORDS[outcome] : undefined) ??
    statusOf(project, outcome)
  );
}

/** The label and tone of the listing's live (design-viz.md section 4). */
export const liveStatus: Record<Live, { label: string; tone: Tone }> = {
  running: { label: "Running", tone: "neutral" },
  "may have stopped": { label: "May have stopped", tone: "warn" },
  "not ended": { label: NOT_ENDED, tone: "neutral" },
};

/** A state's title, else its name as `readable` spells it. */
export function stateTitle(project: Project, state: string): string {
  return project.states[state]?.title ?? readable(state);
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
