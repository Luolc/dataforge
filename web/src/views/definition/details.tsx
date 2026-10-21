// The definition's part of a column, its workflow file and a link to each
// stage's prompts and rubrics, and the workflow file they lead to.
import { CircleDashed, FileCode, FileText } from "lucide-react";
import { Link } from "react-router";

import type { Project } from "../../api/types";
import { CodeView } from "../../ui/code";
import { stateTitle } from "../../ui/status";
import { NotRecorded } from "./entries";
import type { Snapshot } from "./load";
import type { Stage, Workflow } from "./types";
import { NOT_RECORDED_WORD } from "./words";

/** ?defn= this shows the workflow file; any other value names a stage. */
export const WORKFLOW = "workflow";

const columnItem =
  "target flex items-center gap-2 rounded-row px-2 py-1.5 font-medium text-fg-2 hover:bg-hover hover:text-fg aria-[current=page]:bg-active aria-[current=page]:text-fg aria-[current=page]:shadow-current";

const columnHeading = "mb-1 px-2 text-xs font-medium text-fg-3";

/**
 * The column's Definition part: the workflow file and each stage, or, for an
 * attempt with no snapshot, that it is not run (L40). Null while it loads.
 */
export function DefinitionLinks({
  project,
  snapshot,
  href,
  current,
}: {
  project: Project;
  snapshot: Snapshot | null;
  href: (value: string) => string;
  current: string | null;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <h2 className={columnHeading}>Definition</h2>
      {snapshot === null ? null : snapshot.shape === "none" ? (
        <p className="flex items-center gap-2 px-2 py-1.5 text-fg-3">
          <CircleDashed className="size-4 flex-none text-icon" aria-hidden />
          {NOT_RECORDED_WORD}
        </p>
      ) : (
        <>
          <WorkflowLink to={href(WORKFLOW)} current={current === WORKFLOW} />
          {snapshot.shape === "definition" && (
            <StageLinks
              project={project}
              stages={snapshot.definition.stages}
              href={href}
              current={current}
            />
          )}
        </>
      )}
    </div>
  );
}

function WorkflowLink({ to, current }: { to: string; current: boolean }) {
  return (
    <Link
      to={to}
      aria-current={current ? "page" : undefined}
      className={columnItem}
    >
      <FileCode className="size-4 text-icon" aria-hidden />
      Workflow file
    </Link>
  );
}

/** A link to each stage, with how many entries it has. */
function StageLinks({
  project,
  stages,
  href,
  current,
}: {
  project: Project;
  stages: Stage[];
  href: (state: string) => string;
  current: string | null;
}) {
  return stages.map((s) => (
    <Link
      key={s.state}
      to={href(s.state)}
      aria-current={s.state === current ? "page" : undefined}
      className={columnItem}
    >
      <FileText className="size-4 flex-none text-icon" aria-hidden />
      <span className="min-w-0 flex-auto">{stateTitle(project, s.state)}</span>
      <span className="text-2xs text-faint tabular-nums">
        {s.entries.length}
      </span>
    </Link>
  ));
}

const lineCount = (text: string) =>
  (text.endsWith("\n") ? text.slice(0, -1) : text).split("\n").length;

/** The workflow file as it was read, highlighted, under its name. */
export function WorkflowFile({ workflow }: { workflow: Workflow | null }) {
  if (workflow === null)
    return <NotRecorded>This snapshot has no workflow file.</NotRecorded>;
  return (
    <section
      aria-label={workflow.path}
      className="min-w-0 overflow-hidden rounded-panel border border-line bg-card"
    >
      <header className="flex items-baseline justify-between gap-3 border-b border-line bg-head px-5 py-3">
        <h3 className="font-mono text-md font-medium break-all">
          {workflow.path}
        </h3>
        <span className="flex-none text-xs text-fg-3 tabular-nums">
          {`${workflow.format} · ${lineCount(workflow.text).toString()} lines`}
        </span>
      </header>
      <CodeView name={`x.${workflow.format}`} text={workflow.text} />
    </section>
  );
}
