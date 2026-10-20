// Statuses and outcomes drawn by their tone, as the project file names them.
import { Check, Minus, TriangleAlert, X } from "lucide-react";

import type { AttemptRecord, Live, Project, Tone } from "../api/types";

// The words the viewer itself gives a status; every other word comes from the
// project file.
/**
 * An attempt with no status whose last visit did not raise, or a visit with
 * no outcome: running, or its process died, which the record cannot tell.
 */
export const NOT_ENDED = "Not ended";
/** A state, a deliverable or the end that the attempt never reached. */
export const NOT_RUN = "Not run";
/**
 * An attempt that ended with no status word: its status is null and the
 * project names its own words for not ended (design-viz.md section 4).
 */
export const NO_STATUS = "No status";
/** An attempt whose record cannot be read: the listing's error. */
export const UNREADABLE = "Unreadable";
/** The engine's outcome of a visit whose state raised (design.md). */
export const RAISED = "raised";

/**
 * A word's label and tone. A word the project file has no entry for is
 * itself, neutral; but raised, the engine's own word for an exception, is
 * bad, and no status (null) is Not ended, neutral.
 */
export function statusOf(
  project: Project,
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
  return status !== null && !project.not_ended?.includes(status)
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
  unreadable: { label: UNREADABLE, tone: "bad" },
} as const satisfies Record<string, { label: string; tone: Tone }>;

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

const toneLabel: Record<Tone, string> = {
  good: "bg-good-bg text-good",
  bad: "bg-bad-bg text-bad",
  warn: "bg-warn-bg text-warn",
  neutral: "bg-neutral-bg text-fg-2",
};

const toneIcon: Record<Tone, string> = {
  good: "bg-good",
  bad: "bg-bad",
  warn: "bg-warn",
  neutral: "bg-neutral",
};

/** Each tone's icon: no two tones share a shape (L39). */
export const toneGlyph = {
  good: Check,
  bad: X,
  warn: TriangleAlert,
  neutral: Minus,
};

/** A word as a tone-coloured label. */
export function StatusLabel({
  project,
  word,
}: {
  project: Project;
  word: string | null;
}) {
  const { label, tone } = statusOf(project, word);
  return (
    <span
      className={`inline-flex h-5 items-center rounded-row px-2 text-xs font-medium ${toneLabel[tone]}`}
    >
      {label}
    </span>
  );
}

/** A filled circle with a glyph; its label is read out, not shown. */
export function StatusIcon({
  project,
  word,
}: {
  project: Project;
  word: string | null;
}) {
  const { label, tone } = statusOf(project, word);
  const Glyph = toneGlyph[tone];
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={`inline-grid size-4 flex-none place-items-center rounded-full ${toneIcon[tone]}`}
    >
      <Glyph className="size-2.5 text-panel" strokeWidth={3} aria-hidden />
    </span>
  );
}
