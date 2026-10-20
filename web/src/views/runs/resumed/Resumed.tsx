// A resumption in the Runs column, and the page of a visit it discarded
// (design-viz.md section 5, "Resumed attempts").
import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import type { AttemptRecord, Project } from "../../../api/types";
import { Card, Note } from "../../../ui/card";
import { time, usd } from "../../../ui/format";
import { outcomeOf, stateTitle, viewerStatus } from "../../../ui/status";
import FilesView from "../../files";
import { VisitLine } from "../visit-line";
import { discardedAt, type Placed } from "./resumed";

/**
 * A resumption's rows: the folded group of the visits it discarded, then the
 * line where the attempt went on. current is the discarded visit shown.
 */
export function ResumedRows({
  project,
  placed,
  ended,
  href,
  current,
  itemClass,
}: {
  project: Project;
  placed: Placed;
  ended: boolean;
  href: (n: number) => string;
  current: number | null;
  itemClass: string;
}) {
  const { resumption, first } = placed;
  const holdsCurrent =
    current !== null &&
    current >= first &&
    current < first + resumption.discarded.length;
  const [open, setOpen] = useState(holdsCurrent);
  const listId = useId();
  const n = resumption.discarded.length;
  const k = resumption.discarded.filter((visit) => {
    const { tone } = outcomeOf(project, visit.outcome, ended);
    return tone === "bad" || tone === "warn";
  }).length;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <>
      {n > 0 && (
        <div>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={listId}
            onClick={() => {
              setOpen(!open);
            }}
            className="flex target w-full items-center gap-2 rounded-row px-2 py-1.5 text-left text-fg-2 hover:bg-hover hover:text-fg"
          >
            <Chevron className="size-4 flex-none text-icon" aria-hidden />
            <span className="flex-auto">{viewerStatus.discarded.label}</span>
            <span className="text-2xs text-fg-3 tabular-nums">
              {k > 0 ? `${k.toString()} of ${n.toString()} bad or warn` : n}
            </span>
          </button>
          <div id={listId} hidden={!open} className="flex flex-col gap-0.5">
            {resumption.discarded.map((visit, i) =>
              visit.path !== null ? (
                <Link
                  key={`${visit.state}-${visit.visit.toString()}`}
                  to={href(first + i)}
                  aria-current={current === first + i ? "page" : undefined}
                  className={`${itemClass} pl-6`}
                >
                  <VisitLine project={project} visit={visit} ended={ended} />
                </Link>
              ) : (
                <div
                  key={`${visit.state}-${visit.visit.toString()}`}
                  className="px-2 py-1.5 pl-6 text-fg-2"
                >
                  <div className="flex items-center gap-2">
                    <VisitLine project={project} visit={visit} ended={ended} />
                  </div>
                  {visit.reason !== null && (
                    <p className="mt-1 text-2xs text-fg-3">{visit.reason}</p>
                  )}
                </div>
              ),
            )}
          </div>
        </div>
      )}
      <div className="mx-2 my-1 border-t border-line pt-1.5 text-2xs text-fg-3">
        <p className="font-medium text-fg-2">Resumed {time(resumption.at)}</p>
        {resumption.reason !== null && <p>{resumption.reason}</p>}
        {resumption.carried_usd > 0 && (
          <p className="tabular-nums">{usd(resumption.carried_usd)} carried</p>
        )}
      </div>
    </>
  );
}

/** The page of the n-th discarded visit: where it ended, and its files. */
export function DiscardedPage({
  project,
  attempt,
  record,
  n,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  n: number;
}) {
  const found = discardedAt(record, n);
  if (!found) return <Note>The record has no discarded run {n + 1}.</Note>;
  const { resumption, visit } = found;
  const title = `${stateTitle(project, visit.state)} · visit ${visit.visit.toString()}`;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Card title={title} aside={viewerStatus.discarded.label}>
        <p className="text-fg-2">
          Left out when the attempt was resumed at {time(resumption.at)}
          {resumption.reason !== null && `: ${resumption.reason}`}.
        </p>
      </Card>
      <FilesView
        project={project}
        attempt={attempt}
        record={record}
        deliverable={{
          name: `discarded-${n.toString()}`,
          kind: "files",
          title: "Files kept",
          state: visit.state,
          output: null,
          visit: visit.visit,
          visit_index: null,
          path: visit.path,
          reason: visit.reason,
        }}
      />
    </div>
  );
}
