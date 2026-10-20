// What the Runs column shows of a visit: its outcome, state and cost.
import type { Project, Visit } from "../../api/types";
import { duration, usd, visitCost, visitSeconds } from "../../ui/format";
import { outcomeOf, stateTitle } from "../../ui/status";
import { StatusIcon } from "../../ui/status-mark";

export function VisitLine({
  project,
  visit,
  ended,
}: {
  project: Project;
  visit: Visit;
  ended: boolean;
}) {
  const seconds = visitSeconds(visit);
  return (
    <>
      {/* A box one line of the title high, so the icon stands on its first
          line rather than between the two. */}
      <span className="flex h-lh flex-none items-center self-start">
        <StatusIcon project={project} word={visit.outcome} ended={ended} />
      </span>
      <span className="min-w-0 flex-auto">
        <span className="block break-words">
          {stateTitle(project, visit.state)}
        </span>
        <span className="block text-2xs font-normal text-fg-3">
          {outcomeOf(project, visit.outcome, ended).label}
          {seconds !== null && ` · ${duration(seconds)}`}
          {` · ${usd(visitCost(visit))}`}
        </span>
      </span>
    </>
  );
}
