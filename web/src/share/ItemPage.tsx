// The route /<p>/items/<id>: one item, its deliverables each in a tab, with
// nothing to act on but a link to download all of it, where the site has
// one. A deliverable of a kind the site does not draw is left out.
import { Download } from "lucide-react";
import { use } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { useLoaded } from "../api/client";
import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import { FilesCard } from "../views/files";
import { size } from "../views/files/file";
import { ReportCard } from "../views/report";
import {
  itemHref,
  type ItemRecord,
  itemRecord,
  read,
  type ShareDeliverable,
  type ShareProject,
  shownId,
  SignedContext,
  useShareProject,
  useShareSource,
} from "./api";
import { PassAtK } from "./PassAtK";

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
  const source = useShareSource();
  const loaded = useLoaded(JSON.stringify([project.id, item]), (signal) =>
    read(source.record(project.id, item), itemRecord, refused, signal),
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
  const { download } = useShareSource();
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-7 pt-5">
        <h1 className="font-mono text-lg tracking-normal break-all">{shown}</h1>
        {download !== null && record.download !== null && (
          <a
            href={download(project.id, record.id, record.download.path)}
            download
            className="ml-auto inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover"
          >
            <Download className="size-3.5 text-icon" aria-hidden />
            Download all
            <span className="text-fg-3 tabular-nums">
              {size(record.download.size)}
            </span>
          </a>
        )}
      </div>
      <nav
        aria-label="Item"
        className="sticky top-0 z-10 mt-3 flex h-tabs gap-1 border-b border-line bg-panel px-5"
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
