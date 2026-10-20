// The trials view: a deliverable's trials on the left, headed by its value and
// its visit's outcome; on the right the chosen trial's numbers and its trace in
// the session view.
import { type ReactNode, useState } from "react";

import type { Action, Tone, Trial } from "../../api/types";
import { SessionView } from "../../session";
import { Note } from "../../ui/card";
import { duration, span, usd } from "../../ui/format";
import { attemptStatus, toneGlyph, toneText } from "../../ui/status";
import { StatusLabel } from "../../ui/status-mark";
import { DeliverableCard } from "../DeliverableCard";
import type { DeliverableProps } from "../kind";

// A trial's status words, the only place they are named.
const SOLVED = "solved";
const NOT_SOLVED = "not solved";
const EXCEPTION = "exception";
const NO_REWARD = "no reward";

/**
 * A trial's status: solved when its reward is at least 1.0, as the default
 * `solved` accepts; an exception before anything else.
 */
function trialStatus(trial: Trial): { word: string; tone: Tone } {
  const { reward, exception } = trial.result;
  if (exception !== null) return { word: EXCEPTION, tone: "warn" };
  if (reward === null) return { word: NO_REWARD, tone: "neutral" };
  return reward >= 1
    ? { word: SOLVED, tone: "good" }
    : { word: NOT_SOLVED, tone: "bad" };
}

function TrialStatus({
  trial,
  reward = false,
}: {
  trial: Trial;
  reward?: boolean;
}) {
  const { word, tone } = trialStatus(trial);
  const Glyph = toneGlyph[tone];
  return (
    <span
      className={`inline-flex flex-none items-center gap-1 text-xs ${toneText[tone]}`}
    >
      <Glyph className="size-3" aria-hidden />
      {word}
      {reward &&
        trial.result.reward !== null &&
        ` · reward ${String(trial.result.reward)}`}
    </span>
  );
}

/** The trial action of a visit whose outputs hold the trial's output. */
function actionOf(actions: Action[], trial: Trial): Action | undefined {
  return actions.find(
    (action) =>
      action.kind === "trial" && Object.hasOwn(action.outputs, trial.output),
  );
}

export default function TrialsView({
  project,
  attempt,
  record,
  deliverable,
}: DeliverableProps) {
  const trials = deliverable.trials ?? [];
  const [chosen, setChosen] = useState(trials[0]?.id);
  if (deliverable.path === null) return <DeliverableCard item={deliverable} />;
  if (trials.length === 0)
    return <Note>This deliverable lists no trials.</Note>;
  const visit =
    deliverable.visit_index === null
      ? undefined
      : record.visits[deliverable.visit_index];
  const trial = trials.find((t) => t.id === chosen) ?? trials[0];
  const value = deliverable.value;
  const shown =
    typeof value?.value === "number" || typeof value?.value === "string"
      ? String(value.value)
      : "—";
  return (
    <div className="grid grid-cols-[calc(var(--spacing)*64)_1fr] items-start gap-6">
      <nav aria-label="Trials" className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <p className="text-xl font-semibold tabular-nums">
            {shown} / {deliverable.k ?? trials.length} solved
          </p>
          {value?.reason != null && (
            <p className="text-xs text-fg-3">{value.reason}</p>
          )}
          {visit !== undefined && (
            <p className="flex items-center gap-2 text-xs text-fg-3">
              Outcome{" "}
              <StatusLabel
                project={project}
                word={visit.outcome}
                ended={attemptStatus(project, record) !== null}
              />
            </p>
          )}
        </div>
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
                <span className="min-w-0 font-mono text-xs break-all">
                  {t.id}
                </span>
                <TrialStatus trial={t} />
              </button>
            </li>
          ))}
        </ul>
      </nav>
      {trial !== undefined && (
        <TrialPane
          key={trial.id}
          trial={trial}
          action={
            visit === undefined ? undefined : actionOf(visit.actions, trial)
          }
          project={project}
          attempt={attempt}
        />
      )}
    </div>
  );
}

function TrialPane({
  trial,
  action,
  project,
  attempt,
}: {
  trial: Trial;
  action: Action | undefined;
  project: DeliverableProps["project"];
  attempt: string;
}) {
  const seconds =
    action === undefined ? null : span(action.started_at, action.ended_at);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <dl className="flex flex-wrap gap-x-6 gap-y-2 rounded-panel border border-line bg-panel px-5 py-3">
        <Field label="Reward">
          {trial.result.reward === null ? "—" : String(trial.result.reward)}
        </Field>
        <Field label="Exception">{trial.result.exception ?? "None"}</Field>
        <Field label="Duration">
          {seconds === null ? "—" : duration(seconds)}
        </Field>
        <Field label="Cost">
          {action === undefined ? "—" : usd(action.cost_usd)}
        </Field>
        <Field label="Trace format">
          <span className="font-mono text-xs">
            {trial.trace?.format ?? "—"}
          </span>
        </Field>
      </dl>
      {trial.trace === null ? (
        <Note>This trial recorded no trace.</Note>
      ) : (
        <SessionView
          project={project}
          attempt={attempt}
          path={trial.trace.path}
          format={trial.trace.format}
          badge={
            <span className="flex items-center gap-2">
              <span className="font-mono text-sm text-fg">{trial.id}</span>
              <TrialStatus trial={trial} reward />
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
