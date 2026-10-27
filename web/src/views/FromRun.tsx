// The line under a deliverable's title that says which run made it, or the
// directory its path was read from in place of the record's.
import { Link } from "react-router";

import type { AttemptRecord, Project } from "../api/types";
import { stateTitle } from "../ui/status";
import { attemptHref } from "./attempt/href";
import { fromARun, laterFiles, type Placed } from "./source";

/**
 * The directory a deliverable's path was read from in place of the
 * record's; else the run a reached deliverable came from, a link to its run
 * page, and the later files deliverable, if any, with its run; nothing for a
 * deliverable not reached or with no run.
 */
export function FromRun({
  project,
  attempt,
  record,
  deliverable,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  deliverable: Placed;
}) {
  if (deliverable.from_dir !== undefined)
    return (
      <>
        From <code className="font-mono">{deliverable.from_dir}/</code>
      </>
    );
  const at = deliverable.visit_index;
  if (!fromARun(deliverable) || at === null) return null;
  const run = (state: string, index: number) => (
    <Link
      to={attemptHref(project.id, attempt, { tab: "runs", run: index })}
      className="inline-flex target items-center link hover:underline"
    >
      {stateTitle(project, state)} · run {index + 1}
    </Link>
  );
  const later = laterFiles(record, deliverable);
  return (
    <>
      From {run(deliverable.state, at)}
      {later !== null && (
        <>
          , before the {later.title}, which is from{" "}
          {run(later.state, later.index)}
        </>
      )}
    </>
  );
}
