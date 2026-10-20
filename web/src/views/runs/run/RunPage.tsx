import type { AttemptRecord, Project } from "../../../api/types";
import { stateTitle, StatusLabel } from "../../../ui/status";

/** One visit's page; until it is built, the visit's state and outcome. */
export function RunPage({
  project,
  record,
  index,
}: {
  project: Project;
  record: AttemptRecord;
  index: number;
}) {
  const visit = record.visits[index];
  if (visit === undefined) {
    return <p className="text-fg-3">The record has no run {index + 1}.</p>;
  }
  return (
    <section className="flex flex-col gap-3 rounded-panel border border-line p-5">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        {stateTitle(project, visit.state)} · run {index + 1}
        <StatusLabel project={project} word={visit.outcome} />
      </h2>
      <p className="text-fg-3">
        The run page is not built yet: visit {visit.visit} of{" "}
        <code className="font-mono text-xs">{visit.state}</code>, with{" "}
        {visit.actions.length} actions.
      </p>
    </section>
  );
}
