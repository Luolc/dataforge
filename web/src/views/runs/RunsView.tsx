// The Runs tab: a column with the Summary, the definition, each run and where the attempt
// was resumed, beside the page.
import { House } from "lucide-react";
import { Fragment, useEffect, useRef } from "react";
import { Link } from "react-router";

import {
  type AttemptRecord,
  currentOf,
  type LiveInfo,
  type Project,
} from "../../api/types";
import type { Loaded } from "../../api/client";
import { SideColumn, sideEntry as item } from "../../ui/side-column";
import { attemptStatus } from "../../ui/status";
import { attemptHref } from "../attempt/href";
import { Note } from "../../ui/card";
import { DefinitionPane } from "../definition/DefinitionView";
import { DefinitionLinks } from "../definition/details";
import { NotRecorded, Unshaped, VersionLine } from "../definition/entries";
import type { Snapshot } from "../definition/load";
import { NOT_RECORDED } from "../definition/words";
import { DiscardedPage, ResumedRows } from "./resumed/Resumed";
import { placeResumptions } from "./resumed/resumed";
import RunPage from "./run";
import { Summary } from "./summary/Summary";
import { RunningLine, VisitLine } from "./visit-line";

// The run that is not in the record yet is no link: it has no page. The
// column scrolls as a whole only when the runs' list would be shorter than
// its least height.
const runningItem =
  "flex items-center gap-2 rounded-row px-2 py-1.5 font-medium text-fg-2";

export function RunsView({
  project,
  attempt,
  record,
  live = null,
  run,
  discarded = null,
  definition = null,
  defn = null,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  /** How the attempt stands now; null when that is not known. */
  live?: LiveInfo | null;
  run: number | null;
  discarded?: number | null;
  /** The attempt's definition snapshot, under "Run details"; null when the
   * project names none. */
  definition?: Loaded<Snapshot> | null;
  /** The definition's item shown, a stage or WORKFLOW. */
  defn?: string | null;
}) {
  const href = (at: { run?: number; discarded?: number }) =>
    attemptHref(project.id, attempt, { tab: "runs", ...at });
  const defnHref = (value: string) =>
    `${attemptHref(project.id, attempt, { tab: "runs" })}&defn=${encodeURIComponent(value)}`;
  const snapshot = definition?.state === "ok" ? definition.data : null;
  const recorded =
    snapshot?.shape === "definition" ? snapshot.definition : null;
  const ended = attemptStatus(project, record) !== null;
  // The run or discarded visit shown is scrolled to the top of the list,
  // which may reach below the frame until the page scrolls; again once the
  // definition's links above the list have loaded, as they make it shorter.
  const listRef = useRef<HTMLDivElement>(null);
  const definitionState = definition?.state;
  useEffect(() => {
    const box = listRef.current;
    const shown = box?.querySelector<HTMLElement>("[aria-current=page]");
    if (!box || !shown) return;
    const top = shown.offsetTop;
    if (top < box.scrollTop || top > box.scrollTop + box.clientHeight / 2)
      box.scrollTop = top;
  }, [run, discarded, definitionState]);
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
          current={defn === null ? discarded : null}
          itemClass={item}
        />
      ));
  return (
    <div className="px-7 pt-5 pb-12">
      <SideColumn
        label="Runs"
        entries={
          <>
            <Link
              to={href({})}
              aria-current={
                run === null && discarded === null && defn === null
                  ? "page"
                  : undefined
              }
              className={item}
            >
              <House className="size-4" aria-hidden />
              Summary
            </Link>
            {definition !== null && (
              <div className="mt-3 border-b border-line pb-3">
                <DefinitionLinks
                  project={project}
                  snapshot={snapshot}
                  href={defnHref}
                  current={defn}
                />
              </div>
            )}
            <h2 className="mt-3 mb-1 px-2 text-xs font-medium text-fg-3">
              Runs · {runs}
            </h2>
            {/* The runs scroll on their own, so the parts above stay in view. */}
            <div
              // eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex -- a keyboard scrolls the list
              tabIndex={0}
              role="region"
              aria-label="Runs list"
              ref={listRef}
              className="relative flex min-h-run-list flex-auto flex-col gap-0.5 overflow-y-auto rounded-row *:flex-none"
            >
              {record.visits.map((visit, index) => (
                <Fragment key={index}>
                  {resumedAt(index)}
                  <Link
                    to={href({ run: index })}
                    aria-current={
                      run === index && defn === null ? "page" : undefined
                    }
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
                    <span className="font-mono text-2xs text-faint">
                      {`#${String(index + 1)}`}
                    </span>
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
                  <span className="font-mono text-2xs text-faint">
                    {`#${String(runs)}`}
                  </span>
                </div>
              )}
            </div>
          </>
        }
      >
        {definition !== null && defn !== null ? (
          recorded !== null ? (
            <DefinitionPane
              definition={recorded}
              picked={defn}
              filled
              top={
                <VersionLine
                  definition={recorded}
                  project={project.id}
                  from={null}
                />
              }
            />
          ) : snapshot?.shape === "other" ? (
            <Unshaped read={snapshot} />
          ) : snapshot?.shape === "none" ? (
            <NotRecorded>{NOT_RECORDED}</NotRecorded>
          ) : (
            <Note>
              {definition.state === "error" ? definition.message : "Loading…"}
            </Note>
          )
        ) : discarded !== null ? (
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
      </SideColumn>
    </div>
  );
}
