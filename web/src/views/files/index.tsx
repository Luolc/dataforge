// The files view: a deliverable's file tree on the left and a read-only
// preview of the chosen file on the right. The chosen file is the page's
// `file`, a path relative to the attempt, used only when it is under this
// deliverable's path; a directory there opens its first file.
import type { ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router";

import { useFileSource, useLoaded } from "../../api/client";
import type { Deliverable } from "../../api/types";
import { Card, Note } from "../../ui/card";
import { Split } from "../../ui/split";
import { NOT_RUN } from "../../ui/status";
import type { DeliverableProps } from "../kind";
import { FromRun } from "../FromRun";
import { fromARun } from "../source";
import { fileAt, firstFile, isKnownFile } from "./entries";
import { Preview } from "./Preview";
import { Tree } from "./Tree";

/** The files view, for a deliverable of kind files. */
export default function FilesView({
  project,
  attempt,
  record,
  deliverable,
}: DeliverableProps) {
  return (
    <FilesCard
      project={project.id}
      attempt={attempt}
      deliverable={deliverable}
      byline={
        fromARun(deliverable) ? (
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

/** The files view's card, for a caller with no record, as the share site. */
export function FilesCard({
  project,
  attempt,
  deliverable,
  byline,
}: {
  project: string;
  attempt: string;
  deliverable: Pick<Deliverable, "title" | "path" | "reason">;
  /** Where it came from, under the title. */
  byline?: ReactNode;
}) {
  const root = deliverable.path;
  return (
    <Card title={deliverable.title} byline={byline} flush={root !== null}>
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

/** Where the file tree's width a reader sets is kept in localStorage. */
export const WIDTH_KEY = "viz.treeWidth";

/** A file tree of root and the preview of the file chosen in it. */
export function Files({
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
  const state: unknown = useLocation().state;
  const known = chosen !== null && isKnownFile(state, chosen);
  const opened = useLoaded(
    chosen === null || known
      ? null
      : JSON.stringify([project, attempt, chosen]),
    (signal) =>
      chosen === null
        ? Promise.resolve(null)
        : fileAt(source, project, attempt, chosen, signal),
  );
  // The chosen path is shown as it is when the tree's link says it is a
  // file, or when it cannot be told a directory.
  const shown =
    chosen === null
      ? first
      : known || opened.state === "error"
        ? { state: "ok" as const, data: chosen }
        : opened;
  const selected = shown.state === "ok" ? shown.data : null;
  return (
    <Split
      name="tree"
      storageKey={WIDTH_KEY}
      label="Resize the file tree"
      sideClass="border-r border-line bg-tree"
      side={
        // The tree opens the folders above the file first shown.
        (chosen !== null || first.state !== "loading") && (
          <Tree
            project={project}
            attempt={attempt}
            root={root}
            selected={selected ?? chosen}
          />
        )
      }
    >
      {selected !== null ? (
        <Preview
          key={selected}
          project={project}
          attempt={attempt}
          path={selected}
          name={selected.slice(root.length + 1)}
        />
      ) : shown.state === "loading" ? (
        <Note>Loading…</Note>
      ) : shown.state === "error" ? (
        <Note>{shown.message}</Note>
      ) : (
        <Note>No file to show.</Note>
      )}
    </Split>
  );
}
