// An attempt's status, start, total duration and cost, under the heading of
// its page and so on every tab (design-viz.md section 5, "Attempt page").
import type { AttemptRecord, LiveInfo, Project } from "../../api/types";
import {
  attemptTimes,
  counted,
  duration,
  span,
  time,
  usd,
  visitCost,
} from "../../ui/format";
import { Elapsed } from "../../ui/running";
import { attemptStatus, liveStatus, statusOf, toneText } from "../../ui/status";
import { carried } from "../runs/resumed/resumed";

export function AttemptFacts({
  project,
  record,
  live,
}: {
  project: Project;
  record: AttemptRecord;
  /** How the attempt stands now; null when that is not known or it has ended. */
  live: LiveInfo | null;
}) {
  const times = attemptTimes(record);
  // A first visit with no action yet has no start of its own: /live gives
  // the one the attempt's job has.
  const started =
    times.started ??
    (live?.current?.index === 0
      ? (live.current.since ?? undefined)
      : undefined);
  const seconds =
    started !== undefined && times.ended !== undefined
      ? span(started, times.ended)
      : null;
  // While it runs, the total goes on from its start; once it has stopped,
  // or may have, it is the record's.
  const word =
    attemptStatus(project, record) === null ? (live?.live ?? null) : null;
  const status =
    word !== null
      ? liveStatus[word]
      : statusOf(project, attemptStatus(project, record));
  const items = [
    {
      key: "Status",
      value: (
        <span
          className={toneText[status.tone]}
          {...(record.reason === null ? {} : { title: record.reason })}
        >
          {status.label}
        </span>
      ),
    },
    { key: "Started", value: started === undefined ? "—" : time(started) },
    {
      key: "Total duration",
      value:
        word === "running" && started !== undefined ? (
          <Elapsed since={started} />
        ) : seconds === null ? (
          "—"
        ) : (
          duration(seconds)
        ),
    },
    { key: "Cost", value: <Cost record={record} /> },
  ];
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-1 px-7 pt-1.5 text-sm">
      {items.map((item) => (
        <div key={item.key} className="flex items-baseline gap-1.5">
          <dt className="text-fg-3">{item.key}</dt>
          <dd className="font-medium tabular-nums">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The budget's cost and cap, with what resumptions carried over; for a
 * record with no budget, the sum of its runs' costs, with no cap.
 */
function Cost({ record }: { record: AttemptRecord }) {
  const budget = record.budget;
  if (!budget) {
    const runs = record.visits.reduce((sum, v) => sum + visitCost(v), 0);
    return (
      <>
        {`${usd(runs)} of —`}
        <span className="ml-1.5 font-normal text-fg-3">from its runs</span>
      </>
    );
  }
  const carry = carried(record);
  return (
    <>
      {budget.max_usd === null
        ? usd(budget.cost_usd)
        : `${usd(budget.cost_usd)} of ${usd(budget.max_usd)}`}
      {carry.usd > 0 && (
        <span className="ml-1.5 font-normal text-fg-3">
          {`${usd(carry.usd)} carried over ${counted(carry.count, "resumption")}`}
        </span>
      )}
    </>
  );
}
