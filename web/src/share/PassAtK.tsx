// An item's trials: how many were solved and each trial on the left; on the
// right the chosen trial's reward, exception and model, then its trace with
// every fold open and the model alone in its header. Nothing here gives a
// trial's duration or cost.
import { type ReactNode, useState } from "react";

import { SessionView } from "../session";
import { Note } from "../ui/card";
import { toneGlyph, toneText } from "../ui/status";
import { trialStatus } from "../views/trials/status";
import type { ShareDeliverable, ShareProject, ShareTrial } from "./api";

/** A trial as the page names it, from its ID. */
const trialName = (trial: ShareTrial) => `Trial ${trial.id}`;

function TrialStatus({ trial }: { trial: ShareTrial }) {
  const { word, tone } = trialStatus(trial.result, false);
  const Glyph = toneGlyph[tone];
  return (
    <span
      className={`inline-flex flex-none items-center gap-1 text-xs ${toneText[tone]}`}
    >
      <Glyph className="size-3" aria-hidden />
      {word}
    </span>
  );
}

export function PassAtK({
  project,
  item,
  deliverable,
}: {
  project: ShareProject;
  item: string;
  deliverable: ShareDeliverable;
}) {
  const trials = deliverable.trials ?? [];
  const [chosen, setChosen] = useState(trials[0]?.id);
  if (trials.length === 0)
    return <Note>This deliverable lists no trials.</Note>;
  const trial = trials.find((t) => t.id === chosen) ?? trials[0];
  const value = deliverable.value?.value;
  const solved =
    typeof value === "number" || typeof value === "string"
      ? String(value)
      : "—";
  return (
    <div className="grid grid-cols-[calc(var(--spacing)*64)_1fr] items-start gap-6">
      <nav aria-label="Trials" className="flex flex-col gap-3">
        <p className="text-xl font-semibold tabular-nums">
          {solved} / {deliverable.k ?? trials.length} solved
        </p>
        <ul className="flex flex-col gap-0.5">
          {trials.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                aria-pressed={t.id === trial?.id}
                onClick={() => {
                  setChosen(t.id);
                }}
                className="flex target w-full items-center justify-between gap-2 rounded-row px-2 text-left hover:bg-hover aria-pressed:bg-active"
              >
                <span className="min-w-0 break-all">{trialName(t)}</span>
                <TrialStatus trial={t} />
              </button>
            </li>
          ))}
        </ul>
      </nav>
      {trial !== undefined && (
        <TrialPane key={trial.id} project={project} item={item} trial={trial} />
      )}
    </div>
  );
}

function TrialPane({
  project,
  item,
  trial,
}: {
  project: ShareProject;
  item: string;
  trial: ShareTrial;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <dl className="flex flex-wrap gap-x-6 gap-y-2 rounded-panel border border-line bg-panel px-5 py-3">
        <Field label="Reward">
          {trial.result?.reward == null ? "—" : String(trial.result.reward)}
        </Field>
        <Field label="Exception">{trial.result?.exception ?? "—"}</Field>
        <Field label="Model">
          <span className="font-mono text-xs">{trial.model ?? "—"}</span>
        </Field>
      </dl>
      {trial.trace === null ? (
        <Note>This trial recorded no trace.</Note>
      ) : (
        <SessionView
          project={project}
          attempt={item}
          path={trial.trace.path}
          format={trial.trace.format}
          startOpen
          totals="model"
          badge={
            <span className="flex items-center gap-2">
              <span className="text-sm text-fg">{trialName(trial)}</span>
              <TrialStatus trial={trial} />
            </span>
          }
        />
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-fg-3">{label}</dt>
      <dd className="tabular-nums">{children}</dd>
    </div>
  );
}
