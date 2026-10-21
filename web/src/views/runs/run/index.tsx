// One run: the visit's steps, folded, from what it was handed to what it
// handed on (design-viz.md section 5, "Runs").
import { ChevronDown, ChevronRight } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { Link } from "react-router";

import type { Action, AttemptRecord, Project, Visit } from "../../../api/types";
import { SessionView } from "../../../session";
import {
  duration,
  span,
  time,
  usd,
  visitCost,
  visitSeconds,
} from "../../../ui/format";
import { Markdown } from "../../../ui/markdown";
import {
  attemptStatus,
  NOT_ENDED,
  stateTitle,
  outcomeOf,
} from "../../../ui/status";
import { StatusLabel } from "../../../ui/status-mark";
import { attemptHref } from "../../attempt/href";
import { size, useFile } from "../../files/file";
import { ReportMarkdown } from "../../report/markdown";
import { markdownOf, type Output, reportsOf } from "./reports";
import { Step } from "./Step";
import { actionStatus, inputOf, isSession, startsOpen, traceOf } from "./steps";

export interface RunProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  visit: Visit;
  /** The visit's place in the record's visits. */
  index: number;
}

export default function RunPage({
  project,
  attempt,
  record,
  visit,
  index,
}: RunProps) {
  const runHref = (at: number) =>
    attemptHref(project.id, attempt, { tab: "runs", run: at });
  const runLink = (at: number) => {
    const target = record.visits[at];
    return (
      <Link
        to={runHref(at)}
        className="inline-flex target items-center underline decoration-faint underline-offset-3"
      >
        {target ? stateTitle(project, target.state) : "Run"} · run {at + 1}
      </Link>
    );
  };
  const open = startsOpen(project, record, index);
  const seconds = visitSeconds(visit);
  const title = stateTitle(project, visit.state);
  return (
    // Keyed by the run, so another run's steps start folded as theirs do.
    <section
      key={index}
      aria-label={`${title} · run ${index + 1}`}
      className="flex min-w-0 flex-col rounded-panel border border-line bg-panel"
    >
      <header className="px-5 py-3.5">
        <h2 className="flex items-center gap-2 text-md font-semibold">
          {title}
          <StatusLabel
            project={project}
            word={visit.outcome}
            ended={attemptStatus(project, record) !== null}
          />
        </h2>
        <p className="mt-1 text-xs text-fg-3 tabular-nums">
          Run {index + 1} of {record.visits.length} · visit {visit.visit} of{" "}
          {title}
          {visit.round !== undefined && ` · round ${visit.round.toString()}`}
          {seconds !== null && ` · ${duration(seconds)}`}
          {` · ${usd(visitCost(visit))}`}
        </p>
      </header>
      <Reports project={project} attempt={attempt} visit={visit} />
      <InputStep
        project={project}
        attempt={attempt}
        record={record}
        index={index}
        runLink={runLink}
      />
      {visit.actions.map((action, i) => (
        <ActionStep
          key={i}
          project={project}
          attempt={attempt}
          record={record}
          index={index}
          action={action}
          open={open.actions[i] ?? false}
        />
      ))}
      {visit.actions.length === 0 && (
        <p className="border-t border-line-subtle px-5 py-2.5 text-fg-3">
          The visit recorded no actions.
        </p>
      )}
      <OutputStep
        project={project}
        record={record}
        visit={visit}
        index={index}
        open={open.output}
        runLink={runLink}
      />
    </section>
  );
}

const fields = "grid grid-cols-[auto_minmax(0,1fr)] gap-x-10";

function Field({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="py-1 text-fg-3">{name}</dt>
      <dd className="min-w-0 py-1 break-words text-fg-2">{children}</dd>
    </div>
  );
}

/**
 * A visit's data as indented JSON, behind a Raw data toggle that starts open
 * only when no report of the visit shows it in words.
 */
function Data({
  data,
  open: startOpen,
}: {
  data: Record<string, unknown>;
  open: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const bodyId = useId();
  if (Object.keys(data).length === 0) return <>none</>;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => {
          setOpen(!open);
        }}
        className="inline-flex target items-center gap-1 rounded-row text-fg-2 hover:bg-hover"
      >
        <Chevron className="size-4 flex-none text-icon" aria-hidden />
        Raw data
      </button>
      <pre
        id={bodyId}
        hidden={!open}
        className="mt-1 overflow-x-auto rounded-row border border-line-subtle bg-code-bg px-3 py-2 font-mono text-xs whitespace-pre-wrap text-fg-2"
      >
        {open && JSON.stringify(data, null, 2)}
      </pre>
    </>
  );
}

/** A Markdown output, read through the files API and rendered as a report. */
function MarkdownFile({
  project,
  attempt,
  output,
}: {
  project: Project;
  attempt: string;
  output: Output;
}) {
  const loaded = useFile(project.id, attempt, output.path);
  const name = output.path.split("/").at(-1) ?? output.path;
  let body;
  if (loaded.state === "loading") body = "Loading…";
  else if (loaded.state === "error") body = loaded.message;
  else if (loaded.data.kind === "too-large") {
    body = `Not shown: ${loaded.data.reason}`;
  } else if (loaded.data.kind === "missing") {
    body = loaded.data.reason;
  } else if (loaded.data.kind === "binary") {
    body = `Not shown: not UTF-8 text, ${size(loaded.data.size)}.`;
  }
  return (
    <article aria-label={name} className="min-w-0">
      <h3 className="mb-2 flex items-baseline gap-2 text-xs text-fg-3">
        {output.name !== name && (
          <span className="font-medium text-fg-2">{output.name}</span>
        )}
        <code className="font-mono">{name}</code>
        {loaded.state === "ok" &&
          loaded.data.kind === "text" &&
          ` · ${size(loaded.data.size)}`}
      </h3>
      {loaded.state === "ok" && loaded.data.kind === "text" ? (
        <ReportMarkdown project={project} text={loaded.data.text} />
      ) : (
        <p className="text-fg-3">{body}</p>
      )}
    </article>
  );
}

/** The visit's own reports, at the top of its run, before its steps. */
function Reports({
  project,
  attempt,
  visit,
}: {
  project: Project;
  attempt: string;
  visit: Visit;
}) {
  const reports = reportsOf(visit);
  if (reports.length === 0) return null;
  return (
    <div className="flex flex-col gap-5 border-t border-line-subtle px-5 py-4">
      {reports.map((output) => (
        <MarkdownFile
          key={output.path}
          project={project}
          attempt={attempt}
          output={output}
        />
      ))}
    </div>
  );
}

function Outputs({ outputs }: { outputs: Record<string, string> }) {
  const names = Object.keys(outputs);
  if (names.length === 0) return <>none</>;
  return (
    <ul className="flex flex-col gap-1">
      {names.map((name) => (
        <li key={name} className="flex flex-wrap items-baseline gap-x-2">
          <span className="wrap-anywhere text-fg">{name}</span>
          <code className="min-w-0 font-mono text-xs wrap-anywhere text-fg-3">
            {outputs[name]}
          </code>
        </li>
      ))}
    </ul>
  );
}

function InputStep({
  project,
  attempt,
  record,
  index,
  runLink,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  index: number;
  runLink: (at: number) => ReactNode;
}) {
  const input = inputOf(record, index);
  const before = input.kind === "report" ? record.visits[input.from] : null;
  const handed = before ? reportsOf(before) : [];
  return (
    <Step
      name="Input"
      status={{ label: "Input", tone: "neutral" }}
      aside={input.kind === "report" ? "a report" : undefined}
      open={handed.length > 0}
    >
      {input.kind === "none" && (
        <p className="text-fg-3">The visit was handed no report.</p>
      )}
      {input.kind === "unknown" && (
        <p className="text-fg-3">
          This record has no graph, so whether the visit was handed a report is
          not known.
        </p>
      )}
      {input.kind === "report" && before && (
        <dl className={fields}>
          <Field name="Report from">{runLink(input.from)}</Field>
          <Field name="Outcome">
            <StatusLabel
              project={project}
              word={before.outcome}
              ended={attemptStatus(project, record) !== null}
            />
          </Field>
          <Field name="Data">
            <Data data={before.data} open={handed.length === 0} />
          </Field>
          <Field name="Files">
            <Outputs
              outputs={Object.fromEntries(
                before.actions.flatMap((a) => Object.entries(a.outputs)),
              )}
            />
          </Field>
        </dl>
      )}
      {handed.length > 0 && (
        <div className="mt-3 flex flex-col gap-5">
          {handed.map((output) => (
            <MarkdownFile
              key={output.path}
              project={project}
              attempt={attempt}
              output={output}
            />
          ))}
        </div>
      )}
      <p className="mt-2 text-xs text-fg-3">
        The record does not say which other files the visit read.
      </p>
    </Step>
  );
}

function ActionStep({
  project,
  attempt,
  record,
  index,
  action,
  open,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  index: number;
  action: Action;
  open: boolean;
}) {
  const status = actionStatus(action.status);
  const seconds = span(action.started_at, action.ended_at);
  const trace = traceOf(record, index, action);
  const names = Object.keys(action.outputs);
  // A report action's Markdown already shows at the top of the run.
  const markdown = action.kind === "report" ? [] : markdownOf(action);
  return (
    <Step
      name={
        <>
          {action.kind}
          {names.length > 0 && (
            <span className="font-normal text-fg-3"> · {names.join(", ")}</span>
          )}
        </>
      }
      status={status}
      aside={`${seconds === null ? "—" : duration(seconds)} · ${usd(action.cost_usd)}`}
      open={open}
    >
      <dl className={fields}>
        <Field name="Kind">{action.kind}</Field>
        <Field name="Status">{status.label}</Field>
        <Field name="Cost">{usd(action.cost_usd)}</Field>
        <Field name="Started">{time(action.started_at)}</Field>
        <Field name="Ended">{time(action.ended_at)}</Field>
        <Field name="Outputs">
          <Outputs outputs={action.outputs} />
        </Field>
      </dl>
      {typeof action.summary === "string" && (
        <div className="mt-3">
          <Markdown text={action.summary} />
        </div>
      )}
      {markdown.map((output) => (
        <div key={output.path} className="mt-3">
          <MarkdownFile project={project} attempt={attempt} output={output} />
        </div>
      ))}
      {isSession(record, index, action) && (
        <div className="mt-3">
          {trace ? (
            <SessionView
              project={project}
              attempt={attempt}
              path={trace.path}
              format={trace.format}
            />
          ) : (
            <p className="text-fg-3">
              The record names no trace for this session.
            </p>
          )}
        </div>
      )}
    </Step>
  );
}

function OutputStep({
  project,
  record,
  visit,
  index,
  open,
  runLink,
}: {
  project: Project;
  record: AttemptRecord;
  visit: Visit;
  index: number;
  open: boolean;
  runLink: (at: number) => ReactNode;
}) {
  const last = index === record.visits.length - 1;
  const end = attemptStatus(project, record);
  return (
    <Step
      name="Output"
      status={outcomeOf(project, visit.outcome, end !== null)}
      open={open}
    >
      <dl className={fields}>
        <Field name="Outcome">
          <StatusLabel
            project={project}
            word={visit.outcome}
            ended={end !== null}
          />
        </Field>
        <Field name="Next">
          {!last ? (
            runLink(index + 1)
          ) : end === null ? (
            NOT_ENDED
          ) : (
            <>
              End · <StatusLabel project={project} word={end} />
            </>
          )}
        </Field>
        <Field name="Mark">{visit.mark ?? "none"}</Field>
        <Field name="Data">
          <Data data={visit.data} open={reportsOf(visit).length === 0} />
        </Field>
      </dl>
    </Step>
  );
}
