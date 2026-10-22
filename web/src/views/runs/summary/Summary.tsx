// The Summary of an attempt's runs: the top row, the machine with the
// transitions taken, the task's tests and principles, the summaries its
// actions wrote and its deliverables.
import { Link } from "react-router";

import {
  type AttemptRecord,
  currentOf,
  type Deliverable,
  type LiveInfo,
  type Project,
} from "../../../api/types";
import { Card } from "../../../ui/card";
import { Markdown } from "../../../ui/markdown";
import { NOT_RUN, stateTitle } from "../../../ui/status";
import { declaredMissing } from "../../attempt/declared";
import { attemptHref } from "../../attempt/href";
import { KindLine } from "../../registry";
import { Graph } from "./Graph";
import { layout } from "./graph";
import { useMeasure } from "./measure";
import { TaskCard } from "./Task";

interface Props {
  project: Project;
  attempt: string;
  record: AttemptRecord;
}

export function Summary({
  project,
  attempt,
  record,
  live = null,
}: Props & {
  /** How the attempt stands now; null when that is not known. */
  live?: LiveInfo | null;
}) {
  const runHref = (index: number) =>
    attemptHref(project.id, attempt, { tab: "runs", run: index });
  const current = currentOf(record, live);
  const measure = useMeasure();
  const machine = layout(record, project, current, measure);
  const runs = record.visits.length + (current && !current.recorded ? 1 : 0);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <TopRow project={project} record={record} />
      <Card
        title="State machine"
        aside={`${runs.toString()} runs · solid lines are the transitions this attempt took · drag to pan, Ctrl+scroll to zoom`}
        flush
      >
        {machine ? (
          <Graph project={project} layout={machine} runHref={runHref} />
        ) : (
          <p className="p-4 text-fg-3">This record has no graph.</p>
        )}
      </Card>
      <TaskCard project={project} attempt={attempt} />
      <Summaries project={project} record={record} runHref={runHref} />
      <Deliverables project={project} attempt={attempt} record={record} />
    </div>
  );
}

/** What the attempt delivered, and the reason it ended; the page's heading has the rest. */
function TopRow({
  project,
  record,
}: {
  project: Project;
  record: AttemptRecord;
}) {
  const missing = declaredMissing(project, record);
  return (
    <section className="rounded-panel border border-line">
      <dl className="flex flex-wrap gap-x-12 gap-y-3 px-5 py-4">
        <div>
          <dt className="text-xs text-fg-3">Deliverables</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums">
            {deliveredOf(record.deliverables, missing.length)}
          </dd>
        </div>
      </dl>
      {record.reason !== null && (
        <p className="border-t border-line px-5 py-3 text-fg-2">
          {record.reason}
        </p>
      )}
    </section>
  );
}

function Summaries({
  project,
  record,
  runHref,
}: {
  project: Project;
  record: AttemptRecord;
  runHref: (index: number) => string;
}) {
  const actions = record.visits.flatMap((visit) => visit.actions);
  if (
    actions.length > 0 &&
    actions.every((action) => action.summary === undefined)
  )
    return (
      <Card title="Summaries">
        <p className="text-fg-3">This record has no summaries.</p>
      </Card>
    );
  const written = record.visits.flatMap((visit, index) =>
    visit.actions.flatMap((action, i) =>
      typeof action.summary === "string"
        ? [{ visit, index, key: `${index}-${i}`, text: action.summary }]
        : [],
    ),
  );
  if (written.length === 0) return null;
  return (
    <Card
      title="Summaries"
      aside="Written by the actions while the attempt ran"
    >
      <div className="flex flex-col gap-5">
        {written.map(({ visit, index, key, text }) => (
          <article key={key}>
            <h3 className="mb-2 text-xs text-fg-3">
              <Link
                to={runHref(index)}
                className="inline-flex target items-center hover:text-fg"
              >
                {stateTitle(project, visit.state)} · run {index + 1}
              </Link>
            </h3>
            <Markdown text={text} />
          </article>
        ))}
      </div>
    </Card>
  );
}

/**
 * The top row's Deliverables: how many of the record's deliverables have a
 * path, of all of them and those the project declares that it lacks, when
 * the two differ.
 */
function deliveredOf(
  recorded: Deliverable[] | undefined,
  missing: number,
): string {
  if (recorded === undefined) return "—";
  const reached = recorded.filter((item) => item.path !== null).length;
  const all = recorded.length + missing;
  return reached === all
    ? String(reached)
    : `${String(reached)} of ${String(all)}`;
}

function Deliverables({ project, attempt, record }: Props) {
  const missing = declaredMissing(project, record);
  const deliverables = record.deliverables && [
    ...record.deliverables,
    ...missing,
  ];
  if (deliverables === undefined)
    return (
      <Card title="Deliverables">
        <p className="text-fg-3">This record has no deliverables.</p>
      </Card>
    );
  if (deliverables.length === 0) return null;
  return (
    <Card title="Deliverables" aside="What the attempt hands on" flush>
      {/* The table scrolls on its own where the card is narrower than it. */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="h-9 text-left text-xs text-fg-3">
              <th className="px-5 font-normal">Name</th>
              <th className="px-5 font-normal">From</th>
              <th className="px-5 font-normal">What</th>
            </tr>
          </thead>
          <tbody>
            {deliverables.map((item) => {
              const from = missing.includes(item)
                ? "—"
                : item.visit_index === null
                  ? stateTitle(project, item.state)
                  : `${stateTitle(project, item.state)} · run ${item.visit_index + 1}`;
              return (
                <tr
                  key={item.name}
                  className="h-11 border-t border-line-subtle"
                >
                  <td className="px-5">
                    <Link
                      to={attemptHref(project.id, attempt, { tab: item.kind })}
                      className="inline-flex target items-center underline decoration-faint underline-offset-3"
                    >
                      {item.title}
                    </Link>
                  </td>
                  <td className="px-5 text-fg-2">{from}</td>
                  <td className="px-5 text-fg-2">
                    {item.path === null ? (
                      `${NOT_RUN}${item.reason !== null && item.reason !== "" ? `: ${item.reason}` : ""}`
                    ) : (
                      <KindLine
                        project={project.id}
                        attempt={attempt}
                        item={item}
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
