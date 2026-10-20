// A status word drawn as a label or an icon in its tone.
import type { Project, Tone } from "../api/types";
import { outcomeOf, toneGlyph } from "./status";

const toneLabel: Record<Tone, string> = {
  good: "bg-good-label text-good-label-fg",
  bad: "bg-bad-label text-bad-label-fg",
  warn: "bg-warn-label text-warn-label-fg",
  neutral: "bg-neutral-label text-neutral-label-fg",
};

const toneIcon: Record<Tone, string> = {
  good: "text-good",
  bad: "text-bad",
  warn: "text-warn",
  neutral: "text-neutral",
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
  const Glyph = toneGlyph[tone];
  return (
    <span
      className={`inline-flex h-5 items-center gap-1 rounded-row px-2 text-xs font-medium ${toneLabel[tone]}`}
    >
      <Glyph className="size-icon-sm" aria-hidden />
      {label}
    </span>
  );
}

/**
 * A status word as its tone's icon, in outline in the tone's colour; its
 * label is read out, not shown. ended is as for StatusLabel.
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
  return <ToneIcon {...outcomeOf(project, word, ended)} />;
}

/** A label and tone as the tone's icon; the label is read out, not shown. */
export function ToneIcon({ label, tone }: { label: string; tone: Tone }) {
  const Glyph = toneGlyph[tone];
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={`inline-grid size-icon-md flex-none place-items-center ${toneIcon[tone]}`}
    >
      <Glyph className="size-icon-md" aria-hidden />
    </span>
  );
}
