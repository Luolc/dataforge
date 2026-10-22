// The trials view: a deliverable's trials on the left, headed by its value and
// its visit's outcome; on the right the chosen trial's numbers, then its trace
// in the session view, its diff and its tests, each in a tab. The chosen trial
// is the page's `trial`, by its output, else the first.
import { Tabs } from "radix-ui";
import { type ReactNode, useState } from "react";
import { Link, useSearchParams } from "react-router";

import type { Action, LiveInfo, Trial } from "../../api/types";
import { SessionView } from "../../session";
import { Note } from "../../ui/card";
import { DiffView } from "../../ui/diff";
import { duration, span, usd } from "../../ui/format";
import { RunningIcon } from "../../ui/running";
import { attemptStatus, toneGlyph, toneText } from "../../ui/status";
import { StatusLabel } from "../../ui/status-mark";
import { attemptHref } from "../attempt/href";
import { DeliverableCard } from "../DeliverableCard";
import { size, useFile } from "../files/file";
import type { DeliverableProps } from "../kind";
import { RUNNING, trialStatus } from "./status";
import { inTrial } from "./path";
import { type Listed, sourceOf, trialName } from "./source";
import { TrialTests } from "./tests";

function isRunning(live: LiveInfo | null | undefined): boolean {
  return live?.live === "running";
}

function TrialStatus({
  trial,
  live,
  reward = false,
}: {
  trial: Trial;
  live: LiveInfo | null | undefined;
  reward?: boolean;
}) {
  const { word, tone } = trialStatus(trial.result, isRunning(live));
  const Glyph = toneGlyph[tone];
  const value = trial.result?.reward ?? null;
  return (
    <span
      className={`inline-flex flex-none items-center gap-1 text-xs ${toneText[tone]}`}
    >
      {word === RUNNING ? (
        <Spinner small />
      ) : (
        <Glyph className="size-3" aria-hidden />
      )}
      {word}
      {reward && value !== null && ` · reward ${String(value)}`}
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
  live,
}: DeliverableProps) {
  const trials = deliverable.trials ?? [];
  const [query, setQuery] = useSearchParams();
  const chosen = query.get("trial");
  const choose = (output: string) => {
    setQuery((before) => {
      const next = new URLSearchParams(before);
      next.set("deliverable", deliverable.name);
      next.set("trial", output);
      return next;
    });
  };
  const [pane, setPane] = useState<Pane>("trace");
  if (deliverable.path === null) return <DeliverableCard item={deliverable} />;
  if (trials.length === 0)
    return <Note>This deliverable lists no trials.</Note>;
  const visit =
    deliverable.visit_index === null
      ? undefined
      : record.visits[deliverable.visit_index];
  const found = trials.findIndex((t) => t.output === chosen);
  const index = found === -1 ? 0 : found;
  const trial = trials[index];
  const value = deliverable.value;
  const shown =
    typeof value?.value === "number" || typeof value?.value === "string"
      ? String(value.value)
      : "—";
  return (
    // The list goes above the trial where the two do not fit side by side.
    <div className="@container">
      <div className="grid items-start gap-6 @3xl:grid-cols-[calc(var(--spacing)*64)_minmax(0,1fr)]">
        <nav aria-label="Trials" className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <p className="text-xl font-semibold tabular-nums">
              {shown} / {deliverable.k ?? trials.length} solved
            </p>
            {/* A visit that has not ended has no value to read yet. */}
            {value?.reason != null && visit?.outcome !== null && (
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
            {trials.map((t, i) => (
              <li key={t.output}>
                <button
                  type="button"
                  aria-pressed={t.output === trial?.output}
                  onClick={() => {
                    choose(t.output);
                  }}
                  className="flex target w-full items-center justify-between gap-2 rounded-row px-2 text-left hover:bg-hover aria-pressed:bg-active"
                >
                  <span className="min-w-0">{trialName(i)}</span>
                  <TrialStatus trial={t} live={live} />
                </button>
              </li>
            ))}
          </ul>
        </nav>
        {trial !== undefined && (
          <TrialPane
            key={trial.output}
            trial={trial}
            name={trialName(index)}
            live={live}
            action={
              visit === undefined ? undefined : actionOf(visit.actions, trial)
            }
            project={project}
            attempt={attempt}
            source={
              trial.source == null
                ? undefined
                : { path: trial.source, listed: sourceOf(record, trial.source) }
            }
            noTrace={
              project.deliverables?.find((d) => d.name === deliverable.name)
                ?.no_trace ?? null
            }
            pane={pane}
            onPane={setPane}
          />
        )}
      </div>
    </div>
  );
}

type Pane = "trace" | "diff" | "tests";
const PANES: { key: Pane; title: string }[] = [
  { key: "trace", title: "Trace" },
  { key: "diff", title: "Diff" },
  { key: "tests", title: "Tests" },
];

/** A trial's source: its path, and the trial there as the record lists it. */
interface Source {
  path: string;
  listed: Listed | null;
}

function TrialPane({
  trial,
  name,
  live,
  action,
  project,
  attempt,
  source,
  noTrace,
  pane,
  onPane,
}: {
  trial: Trial;
  /** Its name, by its place in the list. */
  name: string;
  live: LiveInfo | null | undefined;
  action: Action | undefined;
  project: DeliverableProps["project"];
  attempt: string;
  /** The trial whose output this one judged again, if it judged one. */
  source: Source | undefined;
  /** The project's words for why a trial of the deliverable has no trace. */
  noTrace: string | null;
  pane: Pane;
  onPane: (pane: Pane) => void;
}) {
  const seconds =
    action === undefined ? null : span(action.started_at, action.ended_at);
  const tests = project.trials?.tests ?? null;
  // Only a project that names its trials' tests has the Tests tab.
  const panes = PANES.filter((p) => p.key !== "tests" || tests !== null);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <dl className="flex flex-wrap gap-x-6 gap-y-2 rounded-panel border border-line bg-panel px-5 py-3">
        <Field label="ID">
          <span className="font-mono text-xs break-all">{trial.id ?? "—"}</span>
        </Field>
        <Field label="Reward">
          {trial.result?.reward == null ? "—" : String(trial.result.reward)}
        </Field>
        <Field label="Exception">{trial.result?.exception ?? "—"}</Field>
        <Field label="Duration">
          {seconds === null ? "—" : duration(seconds)}
        </Field>
        <Field label="Cost">
          {action === undefined ? (
            "—"
          ) : (
            <>
              {usd(action.cost_usd)}
              {/* A runner that reports no cost reports 0. */}
              {action.cost_usd === 0 && (
                <span className="block text-xs text-fg-3">
                  No cost reported.
                </span>
              )}
            </>
          )}
        </Field>
        <Field label="Trace format">
          <span className="font-mono text-xs">
            {trial.trace?.format ?? "—"}
          </span>
        </Field>
        {source !== undefined && (
          <Field label="Source trial">
            <SourceName
              project={project.id}
              attempt={attempt}
              source={source}
            />
          </Field>
        )}
      </dl>
      <Tabs.Root
        value={pane}
        onValueChange={(value) => {
          onPane(panes.find((p) => p.key === value)?.key ?? "trace");
        }}
        className="flex flex-col gap-4"
      >
        <Tabs.List
          aria-label="Trial"
          className="flex h-9 gap-1 border-b border-line"
        >
          {panes.map((p) => (
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
          {trial.result === null ? (
            <Unfinished live={live} />
          ) : trial.trace === null ? (
            <NoTrace
              project={project.id}
              attempt={attempt}
              source={source}
              noTrace={noTrace}
            />
          ) : (
            <SessionView
              project={project}
              attempt={attempt}
              path={trial.trace.path}
              format={trial.trace.format}
              badge={
                <span className="flex items-center gap-2">
                  <span className="text-sm font-medium text-fg">{name}</span>
                  <TrialStatus trial={trial} live={live} reward />
                </span>
              }
            />
          )}
        </Tabs.Content>
        <Tabs.Content value="diff">
          {trial.result === null ? (
            <Unfinished live={live} />
          ) : (
            <TrialDiff project={project} attempt={attempt} trial={trial} />
          )}
        </Tabs.Content>
        {tests !== null && (
          <Tabs.Content value="tests">
            {trial.result === null ? (
              <Unfinished live={live} />
            ) : (
              <TrialTests
                project={project}
                attempt={attempt}
                trial={trial}
                relative={tests}
              />
            )}
          </Tabs.Content>
        )}
      </Tabs.Root>
    </div>
  );
}

/**
 * A trial's source by its deliverable's title and its name, linking to it;
 * its path when no deliverable of the record lists it.
 */
function SourceName({
  project,
  attempt,
  source,
}: {
  project: string;
  attempt: string;
  source: Source;
}) {
  const { listed } = source;
  if (listed === null) {
    return (
      <span className="font-mono text-xs break-all">
        {source.path}{" "}
        <span className="font-sans text-fg-3">(listed by no deliverable)</span>
      </span>
    );
  }
  return (
    <Link
      to={attemptHref(project, attempt, {
        tab: listed.deliverable.kind,
        deliverable: listed.deliverable.name,
        trial: listed.trial.output,
      })}
      className="link hover:underline"
    >
      {listed.deliverable.title} · {trialName(listed.index)}
    </Link>
  );
}

/**
 * Why a finished trial shows no trace: the project's words, when it has
 * them, and the trial whose output it judged again, when it judged one.
 */
function NoTrace({
  project,
  attempt,
  source,
  noTrace,
}: {
  project: string;
  attempt: string;
  source: Source | undefined;
  noTrace: string | null;
}) {
  return (
    <div className="flex flex-col gap-2 p-6 text-fg-3">
      <p>
        {source === undefined
          ? "This trial recorded no trace."
          : "This trial recorded no trace of its own."}
      </p>
      {noTrace !== null && <p>{noTrace}</p>}
      {source !== undefined && (
        <p>
          It judged again the output of{" "}
          <SourceName project={project} attempt={attempt} source={source} />
          {source.listed === null ? "." : ", whose trace is there."}
        </p>
      )}
    </div>
  );
}

/**
 * RunningIcon beside a word that already says running, so it is not read out
 * twice; small, as large as the trial's other status glyphs.
 */
function Spinner({ small = false }: { small?: boolean }) {
  return (
    <span
      aria-hidden
      className={small ? "inline-flex [&_*]:size-3" : "contents"}
    >
      <RunningIcon />
    </span>
  );
}

/** What a trial with no result shows in place of its trace and its diff. */
function Unfinished({ live }: { live: LiveInfo | null | undefined }) {
  return isRunning(live) ? (
    <Note>
      <span className="flex items-center gap-2">
        <Spinner />
        This trial is still running.
      </span>
    </Note>
  ) : (
    <Note>This trial did not finish.</Note>
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
  const path = relative === null ? null : inTrial(trial, relative);
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
