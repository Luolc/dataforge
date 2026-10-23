// The trials view: a deliverable's trials on the left, headed by its value and
// its visit's outcome; on the right the chosen trial's numbers, then its trace
// in the session view, its diff, its tests and its log, each in a tab. The
// chosen trial is the page's `trial`, by its output, else the first.
import { CircleDashed } from "lucide-react";
import { Tabs } from "radix-ui";
import { type ReactNode, useId, useState } from "react";
import { Link, useSearchParams } from "react-router";

import type {
  Action,
  Deliverable,
  LiveInfo,
  Project,
  Trial,
} from "../../api/types";
import { SessionView } from "../../session";
import { Note } from "../../ui/card";
import { control } from "../../ui/chip";
import { CodeView } from "../../ui/code";
import { DiffView } from "../../ui/diff";
import { duration, span, usd } from "../../ui/format";
import { RunningIcon } from "../../ui/running";
import { attemptStatus, statusOf, toneGlyph, toneText } from "../../ui/status";
import { OutcomeLabel } from "../../ui/status-mark";
import { attemptHref } from "../attempt/href";
import { fromARun } from "../source";
import { size, useFile } from "../files/file";
import { FromRun } from "../FromRun";
import { declared, type DeliverableProps, notRun } from "../kind";
import {
  COUNTED,
  NO_VERDICT,
  RUNNING,
  trialStatus,
  WITH_EXCEPTION,
} from "./status";
import { inTrial } from "./path";
import { type Listed, sourceOf, trialName } from "./source";
import { TrialTests } from "./tests";

function isRunning(live: LiveInfo | null | undefined): boolean {
  return live?.live === "running";
}

function TrialStatus({
  project,
  trial,
  judged,
  live,
  reward = false,
}: {
  project: Project;
  trial: Trial;
  /** Whether its deliverable gives its trials' verdicts, and they were read. */
  judged: boolean;
  live: LiveInfo | null | undefined;
  reward?: boolean;
}) {
  const verdict =
    trial.verdict != null
      ? statusOf(project, trial.verdict)
      : judged
        ? NO_VERDICT
        : null;
  const { word, tone, exception } = trialStatus(
    trial.result,
    isRunning(live),
    verdict,
  );
  const Glyph = toneGlyph[tone];
  const Warn = toneGlyph.warn;
  const value = trial.result?.reward ?? null;
  return (
    <span className="inline-flex flex-none items-center gap-1 text-xs">
      <span className={`inline-flex items-center gap-1 ${toneText[tone]}`}>
        {word === RUNNING ? (
          <Spinner small />
        ) : (
          <Glyph className="size-3" aria-hidden />
        )}
        {word}
      </span>
      {exception && (
        <span className={`inline-flex items-center gap-1 ${toneText.warn}`}>
          ·<Warn className="size-3" aria-hidden />
          {WITH_EXCEPTION}
        </span>
      )}
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
  if (deliverable.path === null) return <NotRun deliverable={deliverable} />;
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
  const entry = declared(project, deliverable.name);
  // Verdicts that could not be read at all leave the trials judged by their
  // reward, as a deliverable that gives none does.
  const unreadVerdicts = deliverable.verdicts?.reason ?? null;
  const judged = deliverable.verdicts != null && unreadVerdicts === null;
  return (
    // The list goes above the trial where the two do not fit side by side.
    <div className="@container">
      <div className="grid items-start gap-6 @3xl:grid-cols-[calc(var(--spacing)*64)_minmax(0,1fr)]">
        <nav aria-label="Trials" className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <p className="text-xl font-semibold tabular-nums">
              {shown} / {deliverable.k ?? trials.length}{" "}
              {entry?.count ?? COUNTED}
            </p>
            {fromARun(deliverable) && (
              <p className="text-xs text-fg-3">
                <FromRun
                  project={project}
                  attempt={attempt}
                  record={record}
                  deliverable={deliverable}
                />
              </p>
            )}
            {/* A visit that has not ended has no value to read yet. */}
            {value?.reason != null && visit?.outcome !== null && (
              <Unread reason={value.reason} />
            )}
            {unreadVerdicts !== null && (
              <p className="text-xs break-words text-fg-3">
                The trials' verdicts could not be read, so each shows its
                reward: {unreadVerdicts}
              </p>
            )}
            {visit !== undefined && (
              <p className="flex items-center gap-2 text-xs text-fg-3">
                Outcome{" "}
                <OutcomeLabel
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
                  <TrialStatus
                    project={project}
                    trial={t}
                    judged={judged}
                    live={live}
                  />
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
            noTrace={entry?.no_trace ?? null}
            logs={trials.some((t) => t.log != null)}
            judged={judged}
            pane={pane}
            onPane={setPane}
          />
        )}
      </div>
    </div>
  );
}

type Pane = "trace" | "diff" | "tests" | "log";
const PANES: { key: Pane; title: string }[] = [
  { key: "trace", title: "Trace" },
  { key: "diff", title: "Diff" },
  { key: "tests", title: "Tests" },
  { key: "log", title: "Log" },
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
  logs,
  judged,
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
  /** Whether a trial of the deliverable has a log, and so the Log tab. */
  logs: boolean;
  /** Whether the deliverable gives its trials' verdicts. */
  judged: boolean;
  pane: Pane;
  onPane: (pane: Pane) => void;
}) {
  const seconds =
    action === undefined ? null : span(action.started_at, action.ended_at);
  const tests = project.trials?.tests ?? null;
  // Only a project that names its trials' tests has the Tests tab.
  const panes = PANES.filter(
    (p) => (p.key !== "tests" || tests !== null) && (p.key !== "log" || logs),
  );
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <dl className="flex flex-wrap gap-x-6 gap-y-2 rounded-panel border border-line bg-panel px-5 py-3">
        <Field label="ID">
          <span className="font-mono text-xs break-all">{trial.id ?? "—"}</span>
        </Field>
        <Field label="Output">
          <span className="font-mono text-xs break-all">{trial.output}</span>
        </Field>
        <Field label="Reward">
          {trial.result?.reward == null ? "—" : String(trial.result.reward)}
        </Field>
        <Field label="Trial time">
          {seconds === null ? (
            "—"
          ) : (
            <>
              {duration(seconds)}
              <span className="block text-xs text-fg-3">
                Environment, agent and verifier.
              </span>
            </>
          )}
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
        {source !== undefined && (
          <Field label="Source trial">
            <SourceName
              project={project.id}
              attempt={attempt}
              source={source}
            />
          </Field>
        )}
        <Field label="Exception" wide>
          <Exception text={trial.result?.exception ?? null} />
        </Field>
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
              project={project}
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
                  <TrialStatus
                    project={project}
                    trial={trial}
                    judged={judged}
                    live={live}
                    reward
                  />
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
        {logs && (
          <Tabs.Content value="log">
            {trial.result === null ? (
              <Unfinished live={live} />
            ) : (
              <TrialFile
                project={project.id}
                attempt={attempt}
                path={trial.log ?? null}
                none="This trial recorded no log."
                missing="This trial's log is not there."
                empty="The log is empty."
                view={(text, path) => (
                  <CodeView name={path.split("/").at(-1) ?? path} text={text} />
                )}
              />
            )}
          </Tabs.Content>
        )}
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
      className="inline-flex target items-center link hover:underline"
    >
      {listed.deliverable.title} · {trialName(listed.index)}
    </Link>
  );
}

/**
 * Why a finished trial shows no trace of its own: the project's words, when
 * it has them, and the trial whose output it judged again, when it judged
 * one, with that trial's trace under them when it has one.
 */
function NoTrace({
  project,
  attempt,
  source,
  noTrace,
}: {
  project: DeliverableProps["project"];
  attempt: string;
  source: Source | undefined;
  noTrace: string | null;
}) {
  const sourceTrace = source?.listed?.trial.trace ?? null;
  const shown = source?.listed != null && sourceTrace !== null;
  const words = (failure: string | null) => (
    <div className={`flex flex-col gap-2 p-6 text-fg-3 ${shown ? "pb-0" : ""}`}>
      <p>
        {source === undefined
          ? "This trial recorded no trace."
          : "This trial recorded no trace of its own."}
      </p>
      {noTrace !== null && <p>{noTrace}</p>}
      {source !== undefined && (
        <p>
          It judged again the output of{" "}
          <SourceName project={project.id} attempt={attempt} source={source} />
          {source.listed === null
            ? ", so its trace is not known."
            : sourceTrace === null
              ? ", which recorded no trace either."
              : failure === null
                ? ", whose trace is shown below."
                : `, whose trace could not be read: ${failure}`}
        </p>
      )}
    </div>
  );
  if (source?.listed == null || sourceTrace === null) return words(null);
  return (
    <div className="flex flex-col gap-4">
      <SessionView
        project={project}
        attempt={attempt}
        path={sourceTrace.path}
        format={sourceTrace.format}
        above={words}
        badge={
          <span className="text-sm font-medium text-fg">
            {source.listed.deliverable.title} · {trialName(source.listed.index)}
          </span>
        }
      />
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
  return (
    <TrialFile
      project={project.id}
      attempt={attempt}
      path={relative === null ? null : inTrial(trial, relative)}
      none="The project names no diff for its trials."
      missing="This trial recorded no diff."
      empty="The diff is empty: the trial changed nothing."
      view={(text) => <DiffView text={text} />}
    />
  );
}

/**
 * A file of a trial under its path and size, through the files route, or
 * why it is not shown: none when there is no path, missing with the route's
 * reason when the file is not there, empty for an empty file.
 */
function TrialFile({
  project,
  attempt,
  path,
  none,
  missing,
  empty,
  view,
}: {
  project: string;
  attempt: string;
  path: string | null;
  none: string;
  missing: string;
  empty: string;
  view: (text: string, path: string) => ReactNode;
}) {
  const loaded = useFile(project, attempt, path);
  if (path === null) return <Note>{none}</Note>;
  if (loaded.state === "loading") return <Note>Loading…</Note>;
  if (loaded.state === "error") return <Note>{loaded.message}</Note>;
  const content = loaded.data;
  if (content.kind === "missing") {
    return (
      <Note>
        {missing}
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
      {content.text === "" ? <Note>{empty}</Note> : view(content.text, path)}
    </div>
  );
}

/**
 * An exception's first line, the rest folded under Show all; a dash for
 * none. A long line wraps rather than being cut.
 */
function Exception({ text }: { text: string | null }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  if (text === null) return <>—</>;
  const [first = "", ...rest] = text.split("\n");
  return (
    <span className="flex flex-col items-start gap-1">
      <span
        id={id}
        className="font-mono text-xs wrap-anywhere whitespace-pre-wrap"
      >
        {open ? text : first}
      </span>
      {rest.length > 0 && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => {
            setOpen((o) => !o);
          }}
          className={control}
        >
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </span>
  );
}

/**
 * That the deliverable's value could not be read, with the engine's reason
 * folded under Why.
 */
function Unread({ reason }: { reason: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col items-start gap-1 text-xs text-fg-3">
      <p className="flex items-center gap-2">
        No count was recorded.
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => {
            setOpen((o) => !o);
          }}
          className={control}
        >
          Why
        </button>
      </p>
      <p id={id} hidden={!open} className="font-mono break-words">
        {reason}
      </p>
    </div>
  );
}

/** A deliverable the attempt did not reach: Not run, with why. */
function NotRun({ deliverable }: { deliverable: Pick<Deliverable, "reason"> }) {
  return (
    <p className="flex items-center gap-2 p-6 text-fg-3">
      <CircleDashed className="size-4 flex-none text-icon" aria-hidden />
      {notRun(deliverable)}
    </p>
  );
}

function Field({
  label,
  wide = false,
  children,
}: {
  label: string;
  /** Whether it takes a line of its own. */
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={`flex min-w-0 flex-col gap-0.5 ${wide ? "basis-full" : ""}`}
    >
      <dt className="text-xs text-fg-3">{label}</dt>
      <dd className="tabular-nums">{children}</dd>
    </div>
  );
}
