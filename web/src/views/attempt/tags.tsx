// The values of the project's columns that ask for a tag (`tag` in a column of
// its file) for one attempt, drawn as the home page's table draws them.
import { Tooltip } from "radix-ui";

import { urls, useJson, useRefresh } from "../../api/client";
import { attemptRows, type Project } from "../../api/types";
import { Tag } from "../../ui/tag";
import { columnSpec } from "../home/columns";

export function AttemptTags({
  project,
  attempt,
}: {
  project: Project;
  attempt: string;
}) {
  const wanted = project.columns.some((c) => c.tag === true);
  const rows = useJson(
    wanted ? urls.attempts(project.id) : null,
    attemptRows,
    useRefresh(),
  );
  if (!wanted || rows.state !== "ok") return null;
  const row = rows.data.find((r) => r.id === attempt);
  if (row === undefined) return null;
  const tags = project.columns.flatMap((column, index) => {
    if (column.tag !== true) return [];
    const cell = columnSpec(column, index, () => ({
      value: row.values?.[index],
      read: row.reads?.[index] ?? null,
      queued: row.queued?.[index] ?? null,
    })).cell(row);
    // A column of cases is empty where none of them holds.
    return cell === null
      ? []
      : [
          <Tag key={column.title} title={column.title}>
            {cell}
          </Tag>,
        ];
  });
  return (
    <Tooltip.Provider delayDuration={200}>
      <div className="flex flex-wrap items-center gap-1.5">{tags}</div>
    </Tooltip.Provider>
  );
}
