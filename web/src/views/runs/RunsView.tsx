// The Runs tab: a column with the Summary and each run, beside the page.
import { House } from "lucide-react";
import { Link } from "react-router";

import type { AttemptRecord, Project } from "../../api/types";
import { duration, usd, visitCost, visitSeconds } from "../../ui/format";
import { stateTitle, statusOf, StatusIcon } from "../../ui/status";
import { attemptHref } from "../attempt/href";
import { RunPage } from "./run/RunPage";
import { Summary } from "./summary/Summary";

const item =
  "target flex items-center gap-2 rounded-row px-2 py-1.5 font-medium text-fg-2 hover:bg-hover hover:text-fg aria-[current=page]:bg-active aria-[current=page]:text-fg aria-[current=page]:shadow-current";

export function RunsView({
  project,
  attempt,
  record,
  run,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  run: number | null;
}) {
  const href = (at: { run?: number }) =>
    attemptHref(project.id, attempt, { tab: "runs", ...at });
  return (
    <div className="grid grid-cols-[var(--spacing-runs)_minmax(0,1fr)] gap-6 px-7 pt-5 pb-12">
      <nav aria-label="Runs" className="flex flex-col gap-0.5">
        <Link
          to={href({})}
          aria-current={run === null ? "page" : undefined}
          className={item}
        >
          <House className="size-4" aria-hidden />
          Summary
        </Link>
        <h2 className="mt-3 mb-1 px-2 text-xs font-medium text-fg-3">
          Runs · {record.visits.length}
        </h2>
        {record.visits.map((visit, index) => {
          const seconds = visitSeconds(visit);
          return (
            <Link
              key={index}
              to={href({ run: index })}
              aria-current={run === index ? "page" : undefined}
              className={item}
            >
              <StatusIcon project={project} word={visit.outcome} />
              <span className="min-w-0 flex-auto">
                <span className="block break-words">
                  {stateTitle(project, visit.state)}
                </span>
                <span className="block text-2xs font-normal text-fg-3">
                  {statusOf(project, visit.outcome).label}
                  {seconds !== null && ` · ${duration(seconds)}`}
                  {` · ${usd(visitCost(visit))}`}
                </span>
              </span>
              <span className="font-mono text-2xs text-faint">{index + 1}</span>
            </Link>
          );
        })}
      </nav>
      {run === null ? (
        <Summary project={project} attempt={attempt} record={record} />
      ) : (
        <RunPage project={project} record={record} index={run} />
      )}
    </div>
  );
}
