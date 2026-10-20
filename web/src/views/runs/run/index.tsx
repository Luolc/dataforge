// One run: the visit's steps, folded, from what it was handed to what it
// handed on (design-viz.md section 5, "Runs").
import type { ReactNode } from "react";
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
  statusOf,
  StatusLabel,
} from "../../../ui/status";
import { attemptHref } from "../../attempt/href";
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
        className="target inline-flex items-center underline decoration-faint underline-offset-3"
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
          <StatusLabel project={project} word={visit.outcome} />
        </h2>
        <p className="mt-1 text-xs text-fg-3 tabular-nums">
          Run {index + 1} of {record.visits.length} · visit {visit.visit} of{" "}
          {title} · round {visit.round}
          {seconds !== null && ` · ${duration(seconds)}`}
          {` · ${usd(visitCost(visit))}`}
        </p>
      </header>
      <InputStep
        project={project}
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

function Data({ data }: { data: Record<string, unknown> }) {
  if (Object.keys(data).length === 0) return <>none</>;
  return (
    <pre className="overflow-x-auto rounded-row border border-line-subtle bg-code px-3 py-2 font-mono text-xs whitespace-pre-wrap text-fg-2">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function Outputs({ outputs }: { outputs: Record<string, string> }) {
  const names = Object.keys(outputs);
  if (names.length === 0) return <>none</>;
  return (
    <ul className="flex flex-col gap-1">
      {names.map((name) => (
        <li key={name} className="break-all">
          <span className="text-fg">{name}</span>{" "}
          <code className="font-mono text-xs text-fg-3">{outputs[name]}</code>
        </li>
      ))}
    </ul>
  );
}

function InputStep({
  project,
  record,
  index,
  runLink,
}: {
  project: Project;
  record: AttemptRecord;
  index: number;
  runLink: (at: number) => ReactNode;
}) {
  const input = inputOf(record, index);
  const before = input.kind === "report" ? record.visits[input.from] : null;
  return (
    <Step
      name="Input"
      status={{ label: "Input", tone: "neutral" }}
      aside={input.kind === "report" ? "a report" : undefined}
      open={false}
    >
      {input.kind === "none" && (
        <p className="text-fg-3">The visit was handed no report.</p>
      )}
      {input.kind === "unknown" && (
        <p className="text-fg-3">
          The record has no graph, so whether the visit was handed a report is
          not known.
        </p>
      )}
      {input.kind === "report" && before && (
        <dl className={fields}>
          <Field name="Report from">{runLink(input.from)}</Field>
          <Field name="Outcome">
            <StatusLabel project={project} word={before.outcome} />
          </Field>
          <Field name="Data">
            <Data data={before.data} />
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
      {action.summary !== null && (
        <div className="mt-3">
          <Markdown text={action.summary} />
        </div>
      )}
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
  const end = attemptStatus(record);
  return (
    <Step name="Output" status={statusOf(project, visit.outcome)} open={open}>
      <dl className={fields}>
        <Field name="Outcome">
          <StatusLabel project={project} word={visit.outcome} />
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
          <Data data={visit.data} />
        </Field>
      </dl>
    </Step>
  );
}
