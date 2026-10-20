// The Runs tab: a column with the Summary, each run and where the attempt
// was resumed, beside the page.
import { House } from "lucide-react";
import { Fragment } from "react";
import { Link } from "react-router";

import {
  type AttemptRecord,
  currentOf,
  type LiveInfo,
  type Project,
} from "../../api/types";
import { attemptStatus } from "../../ui/status";
import { attemptHref } from "../attempt/href";
import { Note } from "../../ui/card";
import { DiscardedPage, ResumedRows } from "./resumed/Resumed";
import { placeResumptions } from "./resumed/resumed";
import RunPage from "./run";
import { Summary } from "./summary/Summary";
import { RunningLine, VisitLine } from "./visit-line";

const item =
  "target flex items-center gap-2 rounded-row px-2 py-1.5 font-medium text-fg-2 hover:bg-hover hover:text-fg aria-[current=page]:bg-active aria-[current=page]:text-fg aria-[current=page]:shadow-current";

// The run that is not in the record yet is no link: it has no page.
const runningItem =
  "flex items-center gap-2 rounded-row px-2 py-1.5 font-medium text-fg-2";

export function RunsView({
  project,
  attempt,
  record,
  live = null,
  run,
  discarded = null,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  /** How the attempt stands now; null when that is not known. */
  live?: LiveInfo | null;
  run: number | null;
  discarded?: number | null;
}) {
  const href = (at: { run?: number; discarded?: number }) =>
    attemptHref(project.id, attempt, { tab: "runs", ...at });
  const ended = attemptStatus(project, record) !== null;
  // The visit the attempt is in while it runs: the record's last, or one
  // after it that the record does not hold yet.
  const current = currentOf(record, live);
  const pending = current !== null && !current.recorded ? current : null;
  const runs = record.visits.length + (pending ? 1 : 0);
  const places = placeResumptions(record);
  const resumedAt = (index: number) =>
    places
      .get(index)
      ?.map((placed, i) => (
        <ResumedRows
          key={`resumed-${index.toString()}-${i.toString()}`}
          project={project}
          placed={placed}
          href={(n) => href({ discarded: n })}
          current={discarded}
          itemClass={item}
        />
      ));
  return (
    <div className="grid grid-cols-[var(--spacing-runs)_minmax(0,1fr)] gap-6 px-7 pt-5 pb-12">
      <nav aria-label="Runs" className="flex flex-col gap-0.5">
        <Link
          to={href({})}
          aria-current={run === null && discarded === null ? "page" : undefined}
          className={item}
        >
          <House className="size-4" aria-hidden />
          Summary
        </Link>
        <h2 className="mt-3 mb-1 px-2 text-xs font-medium text-fg-3">
          Runs · {runs}
        </h2>
        {record.visits.map((visit, index) => (
          <Fragment key={index}>
            {resumedAt(index)}
            <Link
              to={href({ run: index })}
              aria-current={run === index ? "page" : undefined}
              className={item}
            >
              <VisitLine
                project={project}
                visit={visit}
                ended={ended}
                since={
                  current !== null &&
                  current.recorded &&
                  current.index === index
                    ? current.since
                    : undefined
                }
              />
              <span className="font-mono text-2xs text-faint">{index + 1}</span>
            </Link>
          </Fragment>
        ))}
        {resumedAt(record.visits.length)}
        {pending && (
          <div className={runningItem}>
            <RunningLine
              project={project}
              state={pending.state}
              since={pending.since}
            />
            <span className="font-mono text-2xs text-faint">{runs}</span>
          </div>
        )}
      </nav>
      {discarded !== null ? (
        <DiscardedPage
          project={project}
          attempt={attempt}
          record={record}
          n={discarded}
        />
      ) : run === null ? (
        <Summary
          project={project}
          attempt={attempt}
          record={record}
          live={live}
        />
      ) : record.visits[run] ? (
        <RunPage
          project={project}
          attempt={attempt}
          record={record}
          visit={record.visits[run]}
          index={run}
        />
      ) : (
        <Note>The record has no run {run + 1}.</Note>
      )}
    </div>
  );
}
