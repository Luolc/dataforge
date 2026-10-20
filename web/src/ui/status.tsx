// Statuses and outcomes drawn by their tone, as the project file names them.
import { Check, Minus, TriangleAlert, X } from "lucide-react";

import type { Project, Tone } from "../api/types";

/** A word's label and tone; a word with no entry is itself, neutral. */
export function statusOf(
  project: Project,
  word: string | null,
): { label: string; tone: Tone } {
  if (word === null) return { label: "Running", tone: "neutral" };
  return project.statuses[word] ?? { label: word, tone: "neutral" };
}

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

const glyph = { good: Check, bad: X, warn: TriangleAlert, neutral: Minus };

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
  const Glyph = glyph[tone];
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
