// The route /<p>/items/<id>: one item, with nothing to act on but a link to
// download all of it, where the site has one; then a tab for each kind of
// its deliverables, as the viewer's attempt page has, each with its count and
// drawn by the viewer's view of the kind, as for people outside the machine
// (OutsideContext). A deliverable of a kind the site does not draw is left
// out.
import { Download } from "lucide-react";
import { use, useMemo } from "react";
import { useParams, useSearchParams } from "react-router";

import { useLoaded } from "../api/client";
import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import { tabKinds } from "../views/attempt/declared";
import { TabNav } from "../views/attempt/TabNav";
import { size } from "../views/files/file";
import { declared, kindTitle, leading } from "../views/kind";
import { OutsideContext } from "../views/outside";
import { KindCount, KindTab } from "../views/registry";
import {
  asProject,
  asRecord,
  itemHref,
  type ItemRecord,
  itemRecord,
  read,
  type ShareProject,
  shownId,
  SignedContext,
  useShareProject,
  useShareSource,
} from "./api";

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
  project: shared,
  record,
  tab,
}: {
  project: ShareProject;
  record: ItemRecord;
  tab: string | null;
}) {
  const project = useMemo(() => asProject(shared), [shared]);
  const attempt = useMemo(() => asRecord(record), [record]);
  const deliverables = record.deliverables;
  const kinds = tabKinds(project, deliverables).filter((k) => KINDS.has(k));
  const itemsOf = (kind: string) => deliverables.filter((d) => d.kind === kind);
  // A link made when a tab was keyed by a deliverable's name opens the tab
  // of that deliverable's kind.
  const named = deliverables.find((d) => d.name === tab)?.kind;
  const current =
    kinds.find((k) => k === tab) ?? kinds.find((k) => k === named) ?? kinds[0];
  const shown = shownId(record);
  const { download } = useShareSource();
  const tabs = kinds.map((kind) => {
    const items = itemsOf(kind);
    const title = kindTitle(project, attempt, kind, items);
    const one = leading(project, attempt, items);
    return {
      key: kind,
      title,
      to: itemHref(project.id, record.id, kind),
      count: one && (
        <KindCount
          kind={kind}
          title={title}
          project={project.id}
          attempt={record.id}
          item={one}
          items={items}
          word={declared(project, one.name)?.count}
        />
      ),
    };
  });
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
      <TabNav label="Item" tabs={tabs} current={current} />
      {current === undefined ? (
        <div className="px-7 pt-5 pb-12">
          <Note>This item has nothing to show.</Note>
        </div>
      ) : (
        <OutsideContext value={true}>
          <KindTab
            key={current}
            kind={current}
            project={project}
            attempt={record.id}
            record={attempt}
            items={itemsOf(current)}
          />
        </OutsideContext>
      )}
    </>
  );
}
