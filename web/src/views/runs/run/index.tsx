import type { AttemptRecord, Project, Visit } from "../../../api/types";
import { stateTitle, StatusLabel } from "../../../ui/status";

export interface RunProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  visit: Visit;
  /** The visit's place in the record's visits. */
  index: number;
}

/** One visit's page; until it is built, the visit's state and outcome. */
export default function RunPage({ project, visit, index }: RunProps) {
  return (
    <section className="flex flex-col gap-3 rounded-panel border border-line p-5">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        {stateTitle(project, visit.state)} · run {index + 1}
        <StatusLabel project={project} word={visit.outcome} />
      </h2>
      <p className="text-fg-3">
        The run page is not built yet: visit {visit.visit} of{" "}
        <code className="font-mono text-xs">{visit.state}</code>, with{" "}
        {visit.actions.length} action{visit.actions.length === 1 ? "" : "s"}.
      </p>
    </section>
  );
}
