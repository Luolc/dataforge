// A project's attempts: the ID, then the project's columns, newest first.
import { Link, useParams } from "react-router";

import { urls, useJson } from "../../api/client";
import { attemptRows, type Column, type Project } from "../../api/types";
import { Frame } from "../../shell/Shell";
import { useProject } from "../../shell/projects";
import { Note } from "../../ui/card";
import { duration, time, usd } from "../../ui/format";
import { StatusLabel } from "../../ui/status";
import { attemptHref } from "../attempt/href";

export function AttemptTable() {
  const { project: id } = useParams();
  const project = useProject(id);
  const rows = useJson(
    id === undefined ? null : urls.attempts(id),
    attemptRows,
  );
  const crumbs = [
    { label: "Projects", to: "/" },
    { label: project.state === "ok" ? project.data.name : (id ?? "") },
  ];
  let body;
  if (project.state === "error") body = <Note>{project.message}</Note>;
  else if (rows.state === "error") body = <Note>{rows.message}</Note>;
  else if (project.state === "loading" || rows.state === "loading")
    body = <Note>Loading…</Note>;
  else if (rows.data.length === 0) body = <Note>No attempts yet.</Note>;
  else
    body = (
      <table className="w-full border-collapse">
        <thead>
          <tr className="h-9 text-left text-xs text-fg-3">
            <th className="px-5 font-normal">ID</th>
            {project.data.columns.map((c) => (
              <th
                key={c.title}
                className={`px-5 font-normal ${numeric(c) ? "text-right" : ""}`}
              >
                {c.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.data.map((row) => (
            <tr
              key={row.id}
              className="h-11 border-t border-line-subtle hover:bg-hover"
            >
              <td className="px-5">
                <Link
                  to={attemptHref(project.data.id, row.id)}
                  className="target inline-flex items-center font-mono text-xs underline decoration-faint underline-offset-3"
                >
                  {row.id}
                </Link>
                {row.reason !== null && (
                  <span className="ml-3 text-xs text-fg-3">{row.reason}</span>
                )}
              </td>
              {project.data.columns.map((c, i) => (
                <td
                  key={c.title}
                  className={`px-5 text-fg-2 tabular-nums ${numeric(c) ? "text-right" : ""}`}
                >
                  <Value
                    project={project.data}
                    column={c}
                    value={row.values[i]}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  return <Frame crumbs={crumbs}>{body}</Frame>;
}

/** Whether a column holds numbers, set right-aligned (L41). */
function numeric(column: Column): boolean {
  return ["number", "usd", "duration"].includes(column.format);
}

function Value({
  project,
  column,
  value,
}: {
  project: Project;
  column: Column;
  value: unknown;
}) {
  if (value === null || value === undefined) return <>—</>;
  if (typeof value === "string" && value in project.statuses) {
    return <StatusLabel project={project} word={value} />;
  }
  switch (column.format) {
    case "number":
      return typeof value === "number"
        ? value.toLocaleString("en-US")
        : plain(value);
    case "usd":
      return typeof value === "number" ? usd(value) : plain(value);
    case "duration":
      return typeof value === "number" ? duration(value) : plain(value);
    case "time":
      return typeof value === "string" ? time(value) : plain(value);
    case "text":
      return plain(value);
  }
}

/** A value of the record as text: a string as itself, else as JSON. */
function plain(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
