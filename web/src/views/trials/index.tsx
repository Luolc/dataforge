// The trials view: a deliverable's trials on the left, headed by its value and
// its visit's outcome; on the right the chosen trial's numbers, then its trace
// in the session view and its diff, each in a tab.
import { Tabs } from "radix-ui";
import { type ReactNode, useState } from "react";

import type { Action, Tone, Trial } from "../../api/types";
import { SessionView } from "../../session";
import { Note } from "../../ui/card";
import { DiffView } from "../../ui/diff";
import { duration, span, usd } from "../../ui/format";
import { attemptStatus, toneGlyph, toneText } from "../../ui/status";
import { StatusLabel } from "../../ui/status-mark";
import { DeliverableCard } from "../DeliverableCard";
import { size, useFile } from "../files/file";
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
  const [pane, setPane] = useState<Pane>("trace");
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
          pane={pane}
          onPane={setPane}
        />
      )}
    </div>
  );
}

type Pane = "trace" | "diff";
const PANES: { key: Pane; title: string }[] = [
  { key: "trace", title: "Trace" },
  { key: "diff", title: "Diff" },
];

function TrialPane({
  trial,
  action,
  project,
  attempt,
  pane,
  onPane,
}: {
  trial: Trial;
  action: Action | undefined;
  project: DeliverableProps["project"];
  attempt: string;
  pane: Pane;
  onPane: (pane: Pane) => void;
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
      <Tabs.Root
        value={pane}
        onValueChange={(value) => {
          onPane(value === "diff" ? "diff" : "trace");
        }}
        className="flex flex-col gap-4"
      >
        <Tabs.List
          aria-label="Trial"
          className="flex h-9 gap-1 border-b border-line"
        >
          {PANES.map((p) => (
            <Tabs.Trigger
              key={p.key}
              value={p.key}
              className="relative flex target items-center px-3 font-medium text-fg-2 hover:text-fg data-[state=active]:text-fg data-[state=active]:after:absolute data-[state=active]:after:inset-x-1.5 data-[state=active]:after:-bottom-px data-[state=active]:after:h-0.5 data-[state=active]:after:rounded-full data-[state=active]:after:bg-accent"
            >
              {p.title}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        {/* Kept mounted, so going back to Trace keeps what was opened in it. */}
        <Tabs.Content value="trace" forceMount hidden={pane !== "trace"}>
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
        </Tabs.Content>
        <Tabs.Content value="diff">
          <TrialDiff project={project} attempt={attempt} trial={trial} />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}

/** The trial's diff, at the project's trials.diff under the trial's path. */
function TrialDiff({
  project,
  attempt,
  trial,
}: {
  project: DeliverableProps["project"];
  attempt: string;
  trial: Trial;
}) {
  const relative = project.trials?.diff ?? null;
  const path =
    relative === null
      ? null
      : trial.path === ""
        ? relative
        : `${trial.path}/${relative}`;
  const loaded = useFile(project.id, attempt, path);
  if (path === null)
    return <Note>The project names no diff for its trials.</Note>;
  if (loaded.state === "loading") return <Note>Loading…</Note>;
  if (loaded.state === "error") return <Note>{loaded.message}</Note>;
  const content = loaded.data;
  if (content.kind === "missing") {
    return (
      <Note>
        This trial recorded no diff.
        <span className="mt-1 block font-mono text-xs break-all">
          {content.reason}
        </span>
      </Note>
    );
  }
  if (content.kind === "too-large")
    return <Note>{`Not shown: ${content.reason}`}</Note>;
  if (content.kind === "binary")
    return <Note>{`Not shown: not UTF-8 text, ${size(content.size)}.`}</Note>;
  return (
    <div className="min-w-0 rounded-panel border border-line bg-panel">
      <header className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-2.5">
        <h3 className="font-mono text-xs break-all">{path}</h3>
        <span className="flex-none text-xs text-fg-3 tabular-nums">
          {size(content.size)}
        </span>
      </header>
      {content.text === "" ? (
        <Note>The diff is empty: the trial changed nothing.</Note>
      ) : (
        <DiffView text={content.text} />
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
