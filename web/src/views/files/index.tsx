// The files view: a deliverable's file tree on the left and a read-only
// preview of the chosen file on the right. The chosen file is the page's
// `file`, a path relative to the attempt, used only when it is under this
// deliverable's path.
import { useSearchParams } from "react-router";

import { useFileSource, useLoaded } from "../../api/client";
import type { Deliverable } from "../../api/types";
import { Card, Note } from "../../ui/card";
import { NOT_RUN } from "../../ui/status";
import type { DeliverableProps } from "../kind";
import { firstFile } from "./entries";
import { Preview } from "./Preview";
import { Tree } from "./Tree";

/** The files view, for a deliverable of kind files. */
export default function FilesView({
  project,
  attempt,
  deliverable,
}: DeliverableProps) {
  return (
    <FilesCard
      project={project.id}
      attempt={attempt}
      deliverable={deliverable}
    />
  );
}

/** The files view's card, for a caller with no record, as the share site. */
export function FilesCard({
  project,
  attempt,
  deliverable,
}: {
  project: string;
  attempt: string;
  deliverable: Pick<Deliverable, "title" | "path" | "reason">;
}) {
  const root = deliverable.path;
  return (
    <Card title={deliverable.title} flush={root !== null}>
      {root === null ? (
        <p className="text-fg-3">
          {`${NOT_RUN}${deliverable.reason !== null && deliverable.reason !== "" ? `: ${deliverable.reason}` : ""}`}
        </p>
      ) : (
        <Files project={project} attempt={attempt} root={root} />
      )}
    </Card>
  );
}

function Files({
  project,
  attempt,
  root,
}: {
  project: string;
  attempt: string;
  root: string;
}) {
  const [query] = useSearchParams();
  const asked = query.get("file");
  const chosen = asked?.startsWith(`${root}/`) === true ? asked : null;
  const source = useFileSource();
  const first = useLoaded(
    chosen === null ? JSON.stringify([project, attempt, root]) : null,
    (signal) => firstFile(source, project, attempt, root, signal),
  );
  const selected = chosen ?? (first.state === "ok" ? first.data : null);
  return (
    <div className="grid grid-cols-[minmax(calc(var(--spacing)*48),calc(var(--spacing)*64))_minmax(0,1fr)]">
      <div className="border-r border-line bg-tree">
        {/* The tree opens the folders above the file first shown. */}
        {(chosen !== null || first.state !== "loading") && (
          <Tree
            project={project}
            attempt={attempt}
            root={root}
            selected={selected}
          />
        )}
      </div>
      {selected !== null ? (
        <Preview
          key={selected}
          project={project}
          attempt={attempt}
          path={selected}
          name={selected.slice(root.length + 1)}
        />
      ) : first.state === "loading" ? (
        <Note>Loading…</Note>
      ) : first.state === "error" ? (
        <Note>{first.message}</Note>
      ) : (
        <Note>No file to show.</Note>
      )}
    </div>
  );
}
