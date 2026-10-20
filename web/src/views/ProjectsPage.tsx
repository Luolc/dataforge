import { Diamond } from "lucide-react";
import { use } from "react";
import { Link } from "react-router";

import { Frame } from "../shell/Shell";
import { ProjectsContext } from "../shell/projects";
import { Note } from "../ui/card";

/** The route /: every project, in the site file's order. */
export function ProjectsPage() {
  const projects = use(ProjectsContext);
  let body;
  if (projects.state === "error") body = <Note>{projects.message}</Note>;
  else if (projects.state === "loading") body = <Note>Loading…</Note>;
  else if (projects.data.length === 0)
    body = <Note>The site has no projects.</Note>;
  else
    body = (
      <ul aria-label="Projects" className="flex flex-col gap-0.5 px-5 py-5">
        {projects.data.map((p) => (
          <li key={p.id}>
            <Link
              to={`/${encodeURIComponent(p.id)}`}
              className="flex target min-h-row items-center gap-2 rounded-row px-2 font-medium link hover:bg-hover hover:underline"
            >
              <Diamond
                className="size-3 flex-none fill-current"
                style={{ color: p.color }}
                aria-hidden
              />
              <span className="min-w-0 break-words">{p.name}</span>
            </Link>
          </li>
        ))}
      </ul>
    );
  return <Frame crumbs={[{ label: "Projects" }]}>{body}</Frame>;
}
