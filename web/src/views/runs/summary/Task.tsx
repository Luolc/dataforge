// The Summary's Task card: what the attempt's task.toml says of the tests the
// task declares, counted by suite and kind, and of its principles.
import { CircleDashed } from "lucide-react";
import { useId, useState } from "react";

import { getJson, urls, useLoaded, useRefresh } from "../../../api/client";
import {
  type AttemptRecord,
  type Deliverable,
  type Project,
  type TaskMeta,
  type TaskPrinciple,
  taskMeta,
} from "../../../api/types";
import { Card } from "../../../ui/card";
import { control } from "../../../ui/chip";
import {
  attemptStatus,
  NOT_RUN,
  stateTitle,
  toneGlyph,
  toneText,
} from "../../../ui/status";
import { cutAt, PRINCIPLE_CUT } from "./cut";
import {
  LEGACY_TESTS,
  noSuite,
  NOT_RECORDED,
  PRINCIPLE_TYPES,
  PROJECT_KIND,
  PROJECT_LABEL,
  SUITES,
  TASK_UNREADABLE,
} from "./task-words";

/**
 * The card of the project's task, for a project that names one. A task in a
 * deliverable is the one the record resolves it to, so a running attempt
 * shows its latest; one the record has not made yet is not read.
 */
export function TaskCard({
  project,
  attempt,
  record,
}: {
  project: Project;
  attempt: string;
  /** The record as the page shows it (asShown). */
  record: AttemptRecord;
}) {
  const refresh = useRefresh();
  const source = project.task ?? null;
  const entry =
    source?.deliverable == null
      ? undefined
      : record.deliverables?.find((d) => d.name === source.deliverable);
  const unmade = entry?.path === null;
  const url = source === null || unmade ? null : urls.task(project.id, attempt);
  // Read again when the record names another directory for it.
  const loaded = useLoaded(
    url === null ? null : `${url}\0${entry?.path ?? ""}`,
    (signal) => getJson(url ?? "", taskMeta, signal),
    refresh,
  );
  if (source === null) return null;
  const from =
    entry?.visit_index == null
      ? null
      : `${stateTitle(project, entry.state)} · run ${(entry.visit_index + 1).toString()}`;
  const legacy = loaded.state === "ok" && loaded.data.legacy;
  const aside = (
    <>
      {from !== null && `${from} · `}
      <span className="font-mono">
        {source.file}
        {legacy && " · the format before tests"}
      </span>
    </>
  );
  return (
    <Card title="Task" aside={aside}>
      {unmade ? (
        <NotThere why={entry.reason ?? ""} />
      ) : loaded.state === "loading" ? (
        <p className="text-fg-3">Loading…</p>
      ) : loaded.state === "error" ? (
        <p className="text-fg-3">{loaded.message}</p>
      ) : loaded.data.read === "not run" ? (
        <NotThere why={notThere(project, record, source, entry, from)} />
      ) : (
        <TaskBody project={project} meta={loaded.data} />
      )}
    </Card>
  );
}

/**
 * Why the task.toml is not there, after "Not run: ": the run its deliverable
 * takes made none, the record has no such deliverable, or the attempt has
 * not written one yet or left none.
 */
function notThere(
  project: Project,
  record: AttemptRecord,
  source: NonNullable<Project["task"]>,
  entry: Deliverable | undefined,
  from: string | null,
): string {
  if (source.deliverable !== null) {
    if (entry !== undefined)
      return `${from ?? entry.title} made no ${source.file}.`;
    const declared = project.deliverables?.find(
      (d) => d.name === source.deliverable,
    );
    return `this record has no ${declared?.title ?? source.deliverable}.`;
  }
  return attemptStatus(project, record) === null
    ? `the attempt has no ${source.file} yet.`
    : `the attempt left no ${source.file}.`;
}

function NotThere({ why }: { why: string }) {
  return (
    <p className="flex items-center gap-2 text-fg-3">
      <CircleDashed className="size-4 flex-none text-icon" aria-hidden />
      {why === "" ? NOT_RUN : `${NOT_RUN}: ${why}`}
    </p>
  );
}

function TaskBody({ project, meta }: { project: Project; meta: TaskMeta }) {
  if (meta.read !== "found" || meta.tests === null || meta.principles === null)
    return <Unread meta={meta} />;
  const tests = meta.tests;
  return (
    <div className="@container flex flex-col gap-5">
      <div className="grid gap-5 @2xl:grid-cols-2">
        {SUITES.map(({ suite, title }) => (
          <SuiteTable
            key={suite}
            project={project}
            suite={suite}
            title={title}
            counts={tests[suite] ?? null}
            legacy={meta.legacy}
          />
        ))}
      </div>
      <Principles principles={meta.principles} legacy={meta.legacy} />
    </div>
  );
}

/** A task.toml that did not read, with its word and why. */
function Unread({ meta }: { meta: TaskMeta }) {
  const word = TASK_UNREADABLE;
  const Glyph = toneGlyph[word.tone];
  return (
    <div className="flex flex-col gap-1">
      <p className={`flex items-center gap-2 ${toneText[word.tone]}`}>
        <Glyph className="size-4 flex-none" aria-hidden />
        {word.label}
      </p>
      {meta.reason !== null && (
        <p className="font-mono text-xs break-all text-fg-3">{meta.reason}</p>
      )}
    </div>
  );
}

const cell = "px-2 py-1.5";
const numeric = `${cell} text-right tabular-nums`;

function SuiteTable({
  project,
  suite,
  title,
  counts,
  legacy,
}: {
  project: Project;
  suite: string;
  title: string;
  counts: Record<string, number> | null;
  legacy: boolean;
}) {
  const heading = (
    <h3 className="mb-1.5 text-xs text-fg-3">
      <span className="font-mono text-fg-2">{suite}</span> · {title}
    </h3>
  );
  if (counts === null)
    return (
      <section>
        {heading}
        <p className="flex items-center gap-2 text-fg-3">
          <CircleDashed className="size-4 flex-none text-icon" aria-hidden />
          {NOT_RECORDED}
        </p>
        <p className="mt-1 text-xs text-fg-3">{noSuite(suite, legacy)}</p>
      </section>
    );
  const label = (kind: string) => project.trials?.kinds?.[kind]?.label ?? kind;
  // The project's own tests are a row of their own, above the kinds the
  // task's tests declare.
  const own = counts[PROJECT_KIND];
  const kinds = Object.entries(counts).filter(([k]) => k !== PROJECT_KIND);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <section>
      {heading}
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-fg-2">
            <th scope="col" className={`${cell} text-left font-medium`}>
              Kind
            </th>
            <th scope="col" className={`${numeric} font-medium`}>
              Tests
            </th>
          </tr>
        </thead>
        <tbody>
          {own !== undefined && (
            <tr className="border-b border-line">
              <th scope="row" className={`${cell} text-left font-normal`}>
                {PROJECT_LABEL}
              </th>
              <td className={numeric}>{own}</td>
            </tr>
          )}
          {kinds.map(([kind, n]) => (
            <tr key={kind} className="border-b border-line-subtle">
              <th scope="row" className={`${cell} text-left font-normal`}>
                {label(kind)}
              </th>
              <td className={numeric}>{n}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-semibold">
            <th scope="row" className={`${cell} text-left`}>
              Total
            </th>
            <td className={numeric}>{total}</td>
          </tr>
        </tfoot>
      </table>
    </section>
  );
}

function Principles({
  principles,
  legacy,
}: {
  principles: TaskPrinciple[];
  legacy: boolean;
}) {
  return (
    <section>
      <h3 className="mb-1.5 text-xs text-fg-3">
        Principles · {principles.length}
      </h3>
      {principles.length === 0 ? (
        <p className="text-fg-3">This task has no principles.</p>
      ) : (
        <ul
          aria-label="Principles"
          className="flex flex-col divide-y divide-line-subtle"
        >
          {principles.map((p) => (
            <Principle key={p.id} principle={p} legacy={legacy} />
          ))}
        </ul>
      )}
    </section>
  );
}

function Principle({
  principle,
  legacy,
}: {
  principle: TaskPrinciple;
  legacy: boolean;
}) {
  const textId = useId();
  const [open, setOpen] = useState(false);
  const cut = cutAt(principle.text, PRINCIPLE_CUT);
  const type =
    principle.type === null
      ? NOT_RECORDED
      : (PRINCIPLE_TYPES[principle.type] ?? principle.type);
  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
      <p>
        <span id={textId}>{open || cut === null ? principle.text : cut}</span>
        {cut !== null && (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={textId}
            onClick={() => {
              setOpen((o) => !o);
            }}
            className={`${control} ml-2 align-baseline`}
          >
            {open ? "Show less" : "Show all"}
          </button>
        )}
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-fg-3">ID</dt>
        <dd className="font-mono">{principle.id}</dd>
        <dt className="text-fg-3">Type</dt>
        <dd>{type}</dd>
        <dt className="text-fg-3">Source</dt>
        <dd className={principle.source === null ? "" : "font-mono"}>
          {principle.source ?? NOT_RECORDED}
        </dd>
        <dt className="text-fg-3">Tests</dt>
        <dd>
          {legacy ? (
            `${NOT_RECORDED}: ${LEGACY_TESTS}`
          ) : principle.tests.length === 0 ? (
            "No test names it."
          ) : (
            <ul className="flex flex-col gap-0.5">
              {principle.tests.map((t) => (
                <li key={t} className="font-mono break-all">
                  {t}
                </li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
    </li>
  );
}
