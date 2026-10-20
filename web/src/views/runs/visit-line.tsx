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
      <Line
        icon={<RunningIcon />}
        title={stateTitle(project, visit.state)}
        sub={
          <>
            {liveStatus.running.label}
            {" · "}
            {since === null ? "—" : <Elapsed since={since} />}
            {` · ${usd(visitCost(visit))}`}
          </>
        }
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
 * The visit a running attempt is in that its record does not hold yet: its
 * state, Running and its time going on from since; it has no cost yet.
 */
export function RunningLine({
  project,
  state,
  since,
}: {
  project: Project;
  state: string;
  since: string | null;
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
