// What the Runs column shows of a visit: its outcome, state and cost.
import type { Project, Visit } from "../../api/types";
import { duration, usd, visitCost, visitSeconds } from "../../ui/format";
import { Elapsed, RunningIcon } from "../../ui/running";
import { liveStatus, outcomeOf, stateTitle } from "../../ui/status";
import { StatusIcon } from "../../ui/status-mark";

/**
 * A visit of the record. With since, it is the visit the attempt runs now:
 * Running, its time going on from since, or "—" when since is null.
 */
export function VisitLine({
  project,
  visit,
  ended,
  since,
}: {
  project: Project;
  visit: Visit;
  ended: boolean;
  since?: string | null | undefined;
}) {
  if (since !== undefined)
    return (
      <RunningLine
        project={project}
        state={visit.state}
        since={since}
        cost={visitCost(visit)}
      />
    );
  const seconds = visitSeconds(visit);
  return (
    <Line
      icon={<StatusIcon project={project} word={visit.outcome} ended={ended} />}
      title={stateTitle(project, visit.state)}
      sub={
        <>
          {outcomeOf(project, visit.outcome, ended).label}
          {seconds !== null && ` · ${duration(seconds)}`}
          {` · ${usd(visitCost(visit))}`}
        </>
      }
    />
  );
}

/**
 * The visit a running attempt is in: its state, Running, its time going on
 * from since, or "—" when since is null, and the cost of its actions so far
 * when the record holds it.
 */
export function RunningLine({
  project,
  state,
  since,
  cost,
}: {
  project: Project;
  state: string;
  since: string | null;
  cost?: number;
}) {
  return (
    <Line
      icon={<RunningIcon />}
      title={stateTitle(project, state)}
      sub={
        <>
          {liveStatus.running.label}
          {" · "}
          {since === null ? "—" : <Elapsed since={since} />}
          {cost !== undefined && ` · ${usd(cost)}`}
        </>
      }
    />
  );
}

function Line({
  icon,
  title,
  sub,
}: {
  icon: React.ReactNode;
  title: string;
  sub: React.ReactNode;
}) {
  return (
    <>
      {/* A box one line of the title high, so the icon stands on its first
          line rather than between the two. */}
      <span className="flex h-lh flex-none items-center self-start">
        {icon}
      </span>
      <span className="min-w-0 flex-auto">
        <span className="block break-words">{title}</span>
        <span className="block text-2xs font-normal text-fg-3 tabular-nums">
          {sub}
        </span>
      </span>
    </>
  );
}
