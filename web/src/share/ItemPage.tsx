// The route /<p>/items/<id>: one item, its deliverables each in a tab, with
// nothing to act on. A deliverable of a kind the site does not draw is left
// out.
import { use } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import type { z } from "zod";

import { urls, useLoaded } from "../api/client";
import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import { FilesCard } from "../views/files";
import { ReportCard } from "../views/report";
import {
  itemHref,
  itemRecord,
  read,
  type ShareDeliverable,
  type ShareProject,
  shownId,
  SignedContext,
  useShareProject,
} from "./api";
import { PassAtK } from "./PassAtK";

type ItemRecord = z.infer<typeof itemRecord>;

/** The kinds of deliverable the site draws, in their tabs. */
const KINDS = new Set(["files", "trials", "report"]);

export function ItemPage() {
  const { project: id = "", item = "" } = useParams();
  const project = useShareProject(id);
  if (project.state === "ok")
    return <Item key={item} project={project.data} item={item} />;
  return (
    <Frame crumbs={[{ label: id }, { label: item }]}>
      <Note>{project.state === "error" ? project.message : "Loading…"}</Note>
    </Frame>
  );
}

function Item({ project, item }: { project: ShareProject; item: string }) {
  const { refused } = use(SignedContext);
  const loaded = useLoaded(JSON.stringify([project.id, item]), (signal) =>
    read(urls.record(project.id, item), itemRecord, refused, signal),
  );
  const [params] = useSearchParams();
  const crumbs = [
    { label: project.name, to: `/${encodeURIComponent(project.id)}` },
    { label: loaded.state === "ok" ? shownId(loaded.data) : item },
  ];
  return (
    <Frame crumbs={crumbs}>
      {loaded.state === "ok" ? (
        <Shown project={project} record={loaded.data} tab={params.get("tab")} />
      ) : (
        <Note>{loaded.state === "error" ? loaded.message : "Loading…"}</Note>
      )}
    </Frame>
  );
}

function Shown({
  project,
  record,
  tab,
}: {
  project: ShareProject;
  record: ItemRecord;
  tab: string | null;
}) {
  const tabs = record.deliverables.filter((d) => KINDS.has(d.kind));
  const current = tabs.find((d) => d.name === tab) ?? tabs[0];
  const shown = shownId(record);
  return (
    <>
      <h1 className="px-7 pt-5 font-mono text-lg tracking-normal">{shown}</h1>
      <nav
        aria-label="Item"
        className="sticky top-0 z-10 mt-3 flex h-11 gap-1 border-b border-line bg-panel px-5"
      >
        {tabs.map((d) => (
          <Link
            key={d.name}
            to={itemHref(project.id, record.id, d.name)}
            aria-current={d === current ? "page" : undefined}
            className="relative flex target items-center gap-2 px-3 font-medium text-fg-2 hover:text-fg aria-[current=page]:text-fg aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-1.5 aria-[current=page]:after:-bottom-px aria-[current=page]:after:h-0.5 aria-[current=page]:after:rounded-full aria-[current=page]:after:bg-accent"
          >
            {d.title}
          </Link>
        ))}
      </nav>
      <div className="flex flex-col gap-3 px-7 pt-5 pb-12">
        {current === undefined ? (
          <Note>This item has nothing to show.</Note>
        ) : (
          <Deliverable
            key={current.name}
            project={project}
            item={record.id}
            deliverable={current}
          />
        )}
      </div>
    </>
  );
}

function Deliverable({
  project,
  item,
  deliverable,
}: {
  project: ShareProject;
  item: string;
  deliverable: ShareDeliverable;
}) {
  switch (deliverable.kind) {
    case "files":
      return (
        <FilesCard
          project={project.id}
          attempt={item}
          deliverable={deliverable}
        />
      );
    case "report":
      return (
        <ReportCard
          project={project}
          attempt={item}
          deliverable={deliverable}
        />
      );
    case "trials":
      return (
        <PassAtK project={project} item={item} deliverable={deliverable} />
      );
    default:
      return null;
  }
}
