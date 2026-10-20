// The report view: a report's Markdown under its title, where a table cell
// whose whole text is a key of the project's statuses shows as its label.
import type { Project } from "../../api/types";
import { Card } from "../../ui/card";
import { Markdown } from "../../ui/markdown";
import { NOT_RUN } from "../../ui/status";
import { StatusLabel } from "../../ui/status-mark";
import { size, useFile } from "../files/file";
import type { DeliverableProps } from "../kind";

/** The report view, for a deliverable of kind report. */
export default function ReportView({
  project,
  attempt,
  deliverable,
}: DeliverableProps) {
  const path = deliverable.path;
  const loaded = useFile(project.id, attempt, path);
  const content = loaded.state === "ok" ? loaded.data : null;
  const aside =
    path === null ? undefined : (
      <>
        <code className="font-mono">{path.split("/").at(-1)}</code>
        {content?.kind === "text" && ` · ${size(content.size)}`}
      </>
    );
  let body;
  if (path === null) {
    body = `${NOT_RUN}${deliverable.reason !== null && deliverable.reason !== "" ? `: ${deliverable.reason}` : ""}`;
  } else if (loaded.state === "loading") body = "Loading…";
  else if (loaded.state === "error") body = loaded.message;
  else if (loaded.data.kind === "too-large") {
    body = `Not shown: ${loaded.data.reason}`;
  } else if (loaded.data.kind === "missing") {
    body = loaded.data.reason;
  } else if (loaded.data.kind === "binary") {
    body = `Not shown: not UTF-8 text, ${size(loaded.data.size)}.`;
  }
  return (
    <Card title={deliverable.title} aside={aside}>
      {content?.kind === "text" ? (
        <Markdown text={content.text} cell={statusCell(project)} />
      ) : (
        <p className="text-fg-3">{body}</p>
      )}
    </Card>
  );
}

/** A cell that is a key of the project's statuses, as its label. */
function statusCell(project: Project) {
  return (text: string) =>
    Object.hasOwn(project.statuses, text) ? (
      <StatusLabel project={project} word={text} />
    ) : null;
}
