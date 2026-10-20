// The Summary of an attempt's runs: the top row, the machine with the
// transitions taken, the summaries its actions wrote and its deliverables.
import { Link } from "react-router";

import {
  type AttemptRecord,
  currentOf,
  type LiveInfo,
  type Project,
} from "../../../api/types";
import { Card } from "../../../ui/card";
import {
  duration,
  span,
  time,
  usd,
  visitEnd,
  visitStart,
} from "../../../ui/format";
import { Markdown } from "../../../ui/markdown";
import { Elapsed } from "../../../ui/running";
import {
  attemptStatus,
  liveStatus,
  NOT_RUN,
  stateTitle,
  statusOf,
  toneText,
} from "../../../ui/status";
import { declaredMissing } from "../../attempt/declared";
import { attemptHref } from "../../attempt/href";
import { KindLine } from "../../registry";
import { carried } from "../resumed/resumed";
import { Graph } from "./Graph";
import { layout } from "./graph";

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
  const machine = layout(record, project, current);
  const runs = record.visits.length + (current && !current.recorded ? 1 : 0);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <TopRow project={project} record={record} live={live} />
      <Card
        title="State machine"
        aside={`${runs.toString()} runs · solid lines are the transitions this attempt took · drag to pan, Ctrl+scroll to zoom`}
        flush
      >
        {machine ? (
          <Graph project={project} layout={machine} runHref={runHref} />
        ) : (
          <p className="rounded-b-panel bg-app p-4 text-fg-3">
            This record has no graph.
          </p>
        )}
      </Card>
      <Summaries project={project} record={record} runHref={runHref} />
      <Deliverables project={project} attempt={attempt} record={record} />
    </div>
  );
}

function TopRow({
  project,
  record,
  live,
}: {
  project: Project;
  record: AttemptRecord;
  live: LiveInfo | null;
}) {
  const first = record.visits[0];
  const last = record.visits.at(-1);
  const started = first ? visitStart(first) : undefined;
  const ended = last ? visitEnd(last) : undefined;
  const seconds =
    started !== undefined && ended !== undefined ? span(started, ended) : null;
  // While it runs, the total goes on from its start; once it has stopped,
  // or may have, it is the record's. A record read after the attempt ended
  // is the record's, whatever /live read before.
  const over = attemptStatus(project, record) !== null;
  const word = over ? null : (live?.live ?? null);
  const running = word === "running";
  const status =
    word !== null
      ? liveStatus[word]
      : statusOf(project, attemptStatus(project, record));
  const budget = record.budget;
  const missing = declaredMissing(project, record);
  const carry = carried(record);
  const items = [
    { key: "Started", value: started === undefined ? "—" : time(started) },
    {
      key: "Status",
      value: <span className={toneText[status.tone]}>{status.label}</span>,
    },
    {
      key: "Total duration",
      value:
        running && started !== undefined ? (
          <Elapsed since={started} />
        ) : seconds === null ? (
          "—"
        ) : (
          duration(seconds)
        ),
    },
    {
      key: "Cost",
      value: !budget ? (
        "This record has no budget."
      ) : (
        <>
          {budget.max_usd === null
            ? usd(budget.cost_usd)
            : `${usd(budget.cost_usd)} of ${usd(budget.max_usd)}`}
          {carry.usd > 0 && (
            <span className="block text-xs font-normal text-fg-3">
              {`${usd(carry.usd)} carried over ${carry.count.toString()} ${carry.count === 1 ? "resumption" : "resumptions"}`}
            </span>
          )}
        </>
      ),
    },
    {
      key: "Deliverables",
      value:
        record.deliverables === undefined
          ? "—"
          : missing.length === 0
            ? record.deliverables.length
            : `${String(record.deliverables.length)} of ${String(record.deliverables.length + missing.length)}`,
    },
  ];
  return (
    <section className="rounded-panel border border-line">
      <dl className="flex flex-wrap gap-x-12 gap-y-3 px-5 py-4">
        {items.map((item) => (
          <div key={item.key}>
            <dt className="text-xs text-fg-3">{item.key}</dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums">
              {item.value}
            </dd>
          </div>
        ))}
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
              <tr key={item.name} className="h-11 border-t border-line-subtle">
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
    </Card>
  );
}
