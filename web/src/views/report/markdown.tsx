// A report's Markdown as the report view draws it, shared with the run page:
// a table cell whose whole text is a key of the project's statuses shows as
// its label.
import type { Project } from "../../api/types";
import { Markdown } from "../../ui/markdown";
import { StatusLabel } from "../../ui/status-mark";

/** Untrusted report Markdown, with status cells drawn as their labels. */
export function ReportMarkdown({
  project,
  text,
}: {
  project: Pick<Project, "statuses">;
  text: string;
}) {
  return (
    <Markdown
      text={text}
      cell={(cell) =>
        Object.hasOwn(project.statuses, cell) ? (
          <StatusLabel project={project} word={cell} />
        ) : null
      }
    />
  );
}
