// A status word drawn as a label or an icon in its tone.
import type { Project, Tone } from "../api/types";
import { outcomeOf, toneGlyph } from "./status";

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

/**
 * A word as a tone-coloured label. With ended, the word is a visit's outcome
 * in an attempt that has ended, so a null word is No outcome.
 */
export function StatusLabel({
  project,
  word,
  ended = false,
}: {
  project: Project;
  word: string | null;
  ended?: boolean;
}) {
  const { label, tone } = outcomeOf(project, word, ended);
  return (
    <span
      className={`inline-flex h-5 items-center rounded-row px-2 text-xs font-medium ${toneLabel[tone]}`}
    >
      {label}
    </span>
  );
}

/**
 * A filled circle with a glyph; its label is read out, not shown. ended is as
 * for StatusLabel.
 */
export function StatusIcon({
  project,
  word,
  ended = false,
}: {
  project: Project;
  word: string | null;
  ended?: boolean;
}) {
  const { label, tone } = outcomeOf(project, word, ended);
  const Glyph = toneGlyph[tone];
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={`inline-grid size-4 flex-none place-items-center rounded-full ${toneIcon[tone]}`}
    >
      <Glyph className="size-2.5 text-on-tone" strokeWidth={3} aria-hidden />
    </span>
  );
}
