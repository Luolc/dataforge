import { Link } from "react-router";

import { urls, useJson } from "../../api/client";
import { attemptRows, type Project } from "../../api/types";
import { Note } from "../../ui/card";
import { attemptHref } from "../attempt/href";

/** A project's home page; until it is built, its attempt IDs. */
export default function Home({ project }: { project: Project }) {
  const rows = useJson(urls.attempts(project.id), attemptRows);
  if (rows.state === "loading") return <Note>Loading…</Note>;
  if (rows.state === "error") return <Note>{rows.message}</Note>;
  return (
    <div className="flex flex-col gap-1 p-7">
      <p className="mb-2 text-fg-3">The home page is not built yet.</p>
      {rows.data.map((row) => (
        <Link
          key={row.id}
          to={attemptHref(project.id, row.id)}
          className="target inline-flex items-center self-start font-mono text-xs underline decoration-faint underline-offset-3"
        >
          {row.id}
        </Link>
      ))}
    </div>
  );
}
