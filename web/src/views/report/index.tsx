// The report view: a report's Markdown under its title, where a table cell
// whose whole text is a key of the project's statuses shows as its label.
import type { ReactNode } from "react";

import type { Deliverable, Project } from "../../api/types";
import { Card } from "../../ui/card";
import { NOT_RUN } from "../../ui/status";
import { size, tooLarge, useFile } from "../files/file";
import type { DeliverableProps } from "../kind";
import { FromRun } from "../FromRun";
import { hasSource } from "../source";
import { ReportMarkdown } from "./markdown";

/** The report view, for a deliverable of kind report. */
export default function ReportView({
  project,
  attempt,
  record,
  deliverable,
}: DeliverableProps) {
  return (
    <ReportCard
      project={project}
      attempt={attempt}
      deliverable={deliverable}
      byline={
        hasSource(deliverable) ? (
          <FromRun
            project={project}
            attempt={attempt}
            record={record}
            deliverable={deliverable}
          />
        ) : undefined
      }
    />
  );
}

/** The report view's card. */
function ReportCard({
  project,
  attempt,
  deliverable,
  byline,
}: {
  project: Pick<Project, "id" | "statuses">;
  attempt: string;
  deliverable: Pick<Deliverable, "title" | "path" | "reason">;
  /** Where it came from, under the title. */
  byline?: ReactNode;
}) {
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
    body = tooLarge(loaded.data.reason, path);
  } else if (loaded.data.kind === "missing") {
    body = loaded.data.reason;
  } else if (loaded.data.kind === "binary") {
    body = `Not shown: not UTF-8 text, ${size(loaded.data.size)}.`;
  }
  return (
    <Card title={deliverable.title} aside={aside} byline={byline}>
      {content?.kind === "text" ? (
        <ReportMarkdown project={project} text={content.text} />
      ) : (
        <p className="text-fg-3">{body}</p>
      )}
    </Card>
  );
}
