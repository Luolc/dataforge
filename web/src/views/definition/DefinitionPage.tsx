// The route /<p>/definition: the snapshot of the project's newest attempt
// that has one (design-viz.md section 5, "Definition page").
import { useParams } from "react-router";

import { useLoaded } from "../../api/client";
import type { Project } from "../../api/types";
import { Frame } from "../../shell/Shell";
import { useProject } from "../../shell/projects";
import { Note } from "../../ui/card";
import { DefinitionView } from "./DefinitionView";
import { NotRecorded, Unshaped } from "./entries";
import { findNewest } from "./load";
import { NO_DEFINITION, NONE_OF_ALL, noneOfNewest } from "./words";

export function DefinitionPage() {
  const { project: id } = useParams();
  const project = useProject(id);
  const crumbs = [
    { label: "Projects", to: "/" },
    {
      label: project.state === "ok" ? project.data.name : (id ?? ""),
      to: `/${encodeURIComponent(id ?? "")}`,
    },
    { label: "Definition" },
  ];
  return (
    <Frame crumbs={crumbs}>
      {project.state !== "ok" ? (
        <Note>{project.state === "error" ? project.message : "Loading…"}</Note>
      ) : project.data.definition == null ? (
        <Note>{NO_DEFINITION}</Note>
      ) : (
        <Newest key={project.data.id} project={project.data} />
      )}
    </Frame>
  );
}

function Newest({ project }: { project: Project }) {
  const newest = useLoaded(project.id, (signal) => findNewest(project, signal));
  if (newest.state === "loading") return <Note>Loading…</Note>;
  if (newest.state === "error") return <Note>{newest.message}</Note>;
  const found = newest.data;
  if (!found.found)
    return (
      <div className="px-7 pt-5">
        <NotRecorded>
          {found.all ? NONE_OF_ALL : noneOfNewest(found.read)}
        </NotRecorded>
      </div>
    );
  return found.snapshot.shape === "other" ? (
    <div className="px-7 pt-5">
      <Unshaped read={found.snapshot} />
    </div>
  ) : (
    <DefinitionView
      project={project}
      definition={found.snapshot.definition}
      from={found.attempt}
    />
  );
}
