import { createContext, use } from "react";

import type { Loaded } from "../api/client";
import type { Project } from "../api/types";

/** The site's projects, as the shell last read them. */
export const ProjectsContext = createContext<Loaded<Project[]>>({
  state: "loading",
});

/** The project with an ID: loading, an error, or the project. */
export function useProject(id: string | undefined): Loaded<Project> {
  const projects = use(ProjectsContext);
  if (projects.state !== "ok") return projects;
  const found = projects.data.find((p) => p.id === id);
  return found
    ? { state: "ok", data: found }
    : { state: "error", message: `No project ${id ?? ""}` };
}
