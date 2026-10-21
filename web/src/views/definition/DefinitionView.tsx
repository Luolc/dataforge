// A definition snapshot beside a column of its stages and, under "Run
// details", its workflow file: the project's Definition page. ?defn= picks a
// stage or the workflow file.
import type { ReactNode } from "react";
import { useSearchParams } from "react-router";

import type { Project } from "../../api/types";
import { DefinitionLinks, WORKFLOW, WorkflowFile } from "./details";
import { EntryCard, VersionLine } from "./entries";
import type { Definition } from "./types";

export function DefinitionView({
  project,
  definition,
  from,
}: {
  project: Project;
  definition: Definition;
  /** The attempt the snapshot is from, on a project's page; null on its own. */
  from: string | null;
}) {
  const [query] = useSearchParams();
  const picked = query.get("defn") ?? definition.stages[0]?.state ?? WORKFLOW;
  const href = (value: string) => {
    const next = new URLSearchParams(query);
    next.set("defn", value);
    return `?${next.toString()}`;
  };
  return (
    <div className="flex flex-col gap-4 px-7 pt-5 pb-12">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        {from !== null && (
          <h1 className="text-xl font-semibold tracking-tight">Definition</h1>
        )}
        <VersionLine definition={definition} project={project.id} from={from} />
      </div>
      <div className="grid grid-cols-[var(--spacing-runs)_minmax(0,1fr)] gap-6">
        <nav
          aria-label="Definition"
          className="sticky top-0 -mx-2 max-h-frame self-start overflow-y-auto rounded-panel bg-tree px-2 py-3"
        >
          <DefinitionLinks
            project={project}
            snapshot={{ shape: "definition", definition }}
            href={href}
            current={picked}
          />
        </nav>
        <DefinitionPane
          definition={definition}
          picked={picked}
          filled={false}
        />
      </div>
    </div>
  );
}

/**
 * What a definition's item shows beside a column: the workflow file, or a
 * stage's entries.
 */
export function DefinitionPane({
  definition,
  picked,
  filled,
  top,
}: {
  definition: Definition;
  picked: string;
  /** Whether the variables show the values the attempt filled in. */
  filled: boolean;
  /** What shows above, such as the version line. */
  top?: ReactNode;
}) {
  const stage = definition.stages.find((s) => s.state === picked);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {top}
      {picked === WORKFLOW ? (
        <WorkflowFile workflow={definition.workflow} />
      ) : stage === undefined ? (
        <p className="text-fg-3">The definition has no stage {picked}.</p>
      ) : (
        stage.entries.map((e, i) => (
          <EntryCard
            // A stage's entries are fixed: the snapshot is read once.
            // eslint-disable-next-line @eslint-react/no-array-index-key
            key={i}
            entry={e}
            filled={filled}
          />
        ))
      )}
    </div>
  );
}
