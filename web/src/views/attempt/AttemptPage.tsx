// An attempt: its header, a row of tabs (Runs, then one per deliverable kind
// in the order its first deliverable is declared) and the current tab.
import { useParams, useSearchParams } from "react-router";
import { Link } from "react-router";

import { urls, useJson } from "../../api/client";
import { type AttemptRecord, type Project, record } from "../../api/types";
import { Frame } from "../../shell/Shell";
import { useProject } from "../../shell/projects";
import { Note } from "../../ui/card";
import { attemptStatus, StatusIcon } from "../../ui/status";
import { kindOf, KindTab } from "../registry";
import { RunsView } from "../runs/RunsView";
import { attemptHref } from "./href";

export function AttemptPage() {
  const { project: id } = useParams();
  const [query] = useSearchParams();
  const attempt = query.get("id");
  const project = useProject(id);
  const loaded = useJson(
    id !== undefined && attempt !== null ? urls.record(id, attempt) : null,
    record,
  );
  const crumbs = [
    { label: "Projects", to: "/" },
    {
      label: project.state === "ok" ? project.data.name : (id ?? ""),
      to: `/${encodeURIComponent(id ?? "")}`,
    },
    { label: attempt ?? "" },
  ];
  let body;
  if (attempt === null) body = <Note>No attempt is named.</Note>;
  else if (project.state === "error") body = <Note>{project.message}</Note>;
  else if (loaded.state === "error") body = <Note>{loaded.message}</Note>;
  else if (project.state === "loading" || loaded.state === "loading")
    body = <Note>Loading…</Note>;
  else
    body = (
      <Attempt
        project={project.data}
        attempt={attempt}
        record={loaded.data}
        tab={query.get("tab") ?? "runs"}
        run={runOf(query.get("run"))}
      />
    );
  return <Frame crumbs={crumbs}>{body}</Frame>;
}

function runOf(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) return null;
  return Number(value);
}

function Attempt({
  project,
  attempt,
  record,
  tab,
  run,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  tab: string;
  run: number | null;
}) {
  const kinds = [...new Set(record.deliverables.map((d) => d.kind))];
  const itemsOf = (kind: string) =>
    record.deliverables.filter((d) => d.kind === kind);
  const tabs = [
    { key: "runs", title: "Runs", count: <>{record.visits.length}</> },
    ...kinds.map((kind) => {
      const items = itemsOf(kind);
      const { Count } = kindOf(kind);
      const first = items[0];
      return {
        key: kind,
        title: first?.title ?? kind,
        count: first && (
          <Count
            project={project.id}
            attempt={attempt}
            item={first}
            items={items}
          />
        ),
      };
    }),
  ];
  return (
    <>
      <div className="flex items-center gap-2 px-7 pt-5">
        <StatusIcon project={project} word={attemptStatus(project, record)} />
        <h1 className="text-xl font-semibold tracking-tight">
          Attempt{" "}
          <span className="font-mono text-lg font-normal tracking-normal text-fg-3">
            {attempt}
          </span>
        </h1>
      </div>
      <nav
        aria-label="Attempt"
        className="sticky top-0 z-10 mt-3 flex h-11 gap-1 border-b border-line bg-panel px-5"
      >
        {tabs.map((t) => (
          <Link
            key={t.key}
            to={attemptHref(project.id, attempt, { tab: t.key })}
            aria-current={t.key === tab ? "page" : undefined}
            className="target relative flex items-center gap-2 px-3 font-medium text-fg-2 hover:text-fg aria-[current=page]:text-fg aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-1.5 aria-[current=page]:after:-bottom-px aria-[current=page]:after:h-0.5 aria-[current=page]:after:rounded-full aria-[current=page]:after:bg-accent"
          >
            {t.title}
            <span className="min-w-5 rounded-full bg-active px-1.5 py-px text-center text-2xs text-fg-2 tabular-nums">
              {t.count}
            </span>
          </Link>
        ))}
      </nav>
      {tab === "runs" ? (
        <RunsView
          project={project}
          attempt={attempt}
          record={record}
          run={run}
        />
      ) : kinds.includes(tab) ? (
        <KindTab
          key={tab}
          kind={tab}
          project={project}
          attempt={attempt}
          record={record}
          items={itemsOf(tab)}
        />
      ) : (
        <Note>This attempt has no deliverable of kind {tab}.</Note>
      )}
    </>
  );
}
