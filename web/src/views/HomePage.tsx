import { useParams } from "react-router";

import { Frame } from "../shell/Shell";
import { useProject } from "../shell/projects";
import { Note } from "../ui/card";
import Home from "./home";

/** The route /<p>: the project's home page under its breadcrumb. */
export function HomePage() {
  const { project: id } = useParams();
  const project = useProject(id);
  const crumbs = [
    { label: "Projects", to: "/" },
    { label: project.state === "ok" ? project.data.name : (id ?? "") },
  ];
  return (
    <Frame crumbs={crumbs}>
      {project.state === "ok" ? (
        <Home project={project.data} />
      ) : (
        <Note>{project.state === "error" ? project.message : "Loading…"}</Note>
      )}
    </Frame>
  );
}
