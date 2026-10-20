// An attempt: its header, a row of tabs (Runs, then one per deliverable kind
// in the order its first deliverable is declared) and the current tab.
import type { ReactNode } from "react";
import { useParams, useSearchParams } from "react-router";
import { Link } from "react-router";

import { getJson, urls, useLoaded } from "../../api/client";
import { z } from "zod";

import {
  type AttemptRecord,
  deliverablesOf,
  type Project,
  readRecord,
} from "../../api/types";
import { type Crumb, Frame } from "../../shell/Shell";
import { useProject } from "../../shell/projects";
import { Note } from "../../ui/card";
import { CodeView } from "../../ui/code";
import { attemptStatus, stateTitle } from "../../ui/status";
import { StatusIcon } from "../../ui/status-mark";
import { KindCount, KindTab } from "../registry";
import { RunsView } from "../runs/RunsView";
import { attemptHref } from "./href";

export function AttemptPage() {
  const { project: id } = useParams();
  const [query] = useSearchParams();
  const attempt = query.get("id");
  const project = useProject(id);
  const url =
    id !== undefined && attempt !== null ? urls.record(id, attempt) : null;
  const loaded = useLoaded(url, (signal) =>
    getJson(url ?? "", z.unknown(), signal).then(readRecord),
  );
  const tab = query.get("tab") ?? "runs";
  const run = runOf(query.get("run"));
  const discarded = runOf(query.get("discarded"));
  const summary = tab === "runs" && run === null && discarded === null;
  const crumbs: Crumb[] = [
    { label: "Projects", to: "/" },
    {
      label: project.state === "ok" ? project.data.name : (id ?? ""),
      to: `/${encodeURIComponent(id ?? "")}`,
    },
    {
      label: attempt ?? "",
      ...(id !== undefined && attempt !== null && !summary
        ? { to: attemptHref(id, attempt) }
        : {}),
    },
  ];
  const visit =
    run !== null && loaded.state === "ok" && loaded.data.shape === "record"
      ? loaded.data.record.visits[run]
      : undefined;
  if (run !== null && visit && project.state === "ok")
    crumbs.push({
      label: `Run ${String(run + 1)} · ${stateTitle(project.data, visit.state)}`,
    });
  let body;
  if (attempt === null) body = <Note>No attempt is named.</Note>;
  else if (project.state === "error") body = <Note>{project.message}</Note>;
  else if (loaded.state === "error") body = <Note>{loaded.message}</Note>;
  else if (project.state === "loading" || loaded.state === "loading")
    body = <Note>Loading…</Note>;
  else if (loaded.data.shape === "other")
    body = (
      <OtherRecord
        project={project.data}
        attempt={attempt}
        raw={loaded.data.raw}
        why={loaded.data.why}
      />
    );
  else
    body = (
      <Attempt
        project={project.data}
        attempt={attempt}
        record={loaded.data.record}
        tab={tab}
        run={run}
        discarded={discarded}
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
  discarded,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  tab: string;
  run: number | null;
  discarded: number | null;
}) {
  const deliverables = deliverablesOf(record);
  const kinds = [...new Set(deliverables.map((d) => d.kind))];
  const itemsOf = (kind: string) => deliverables.filter((d) => d.kind === kind);
  const tabs = [
    { key: "runs", title: "Runs", count: <>{record.visits.length}</> },
    ...kinds.map((kind) => {
      const items = itemsOf(kind);
      const first = items[0];
      return {
        key: kind,
        title: first?.title ?? kind,
        count: first && (
          <KindCount
            kind={kind}
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
      <Header attempt={attempt}>
        <StatusIcon project={project} word={attemptStatus(project, record)} />
      </Header>
      <Tabs project={project} attempt={attempt} tabs={tabs} current={tab} />
      {tab === "runs" ? (
        <RunsView
          project={project}
          attempt={attempt}
          record={record}
          run={run}
          discarded={discarded}
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

interface Tab {
  key: string;
  title: string;
  count?: ReactNode;
}

function Header({
  attempt,
  children,
}: {
  attempt: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 px-7 pt-5">
      {children}
      <h1 className="text-xl font-semibold tracking-tight">
        Attempt{" "}
        <span className="font-mono text-lg font-normal tracking-normal text-fg-3">
          {attempt}
        </span>
      </h1>
    </div>
  );
}

function Tabs({
  project,
  attempt,
  tabs,
  current,
}: {
  project: Project;
  attempt: string;
  tabs: Tab[];
  current: string;
}) {
  return (
    <nav
      aria-label="Attempt"
      className="sticky top-0 z-10 mt-3 flex h-11 gap-1 border-b border-line bg-panel px-5"
    >
      {tabs.map((t) => (
        <Link
          key={t.key}
          to={attemptHref(project.id, attempt, { tab: t.key })}
          aria-current={t.key === current ? "page" : undefined}
          className="relative flex target items-center gap-2 px-3 font-medium text-fg-2 hover:text-fg aria-[current=page]:text-fg aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-1.5 aria-[current=page]:after:-bottom-px aria-[current=page]:after:h-0.5 aria-[current=page]:after:rounded-full aria-[current=page]:after:bg-accent"
        >
          {t.title}
          {t.count !== undefined && (
            <span className="min-w-5 rounded-full bg-active px-1.5 py-px text-center text-2xs text-fg-2 tabular-nums">
              {t.count}
            </span>
          )}
        </Link>
      ))}
    </nav>
  );
}

/**
 * A record that is a JSON object of another shape: the Runs tab only, with
 * the record as written, as text (design-viz.md section 5, "Older records").
 */
function OtherRecord({
  project,
  attempt,
  raw,
  why,
}: {
  project: Project;
  attempt: string;
  raw: object;
  why: string;
}) {
  return (
    <>
      <Header attempt={attempt} />
      <Tabs
        project={project}
        attempt={attempt}
        tabs={[{ key: "runs", title: "Runs" }]}
        current="runs"
      />
      <div className="flex flex-col gap-4 px-7 pt-5 pb-12">
        <p className="text-fg-2">
          This record is not in a shape the viewer knows ({why}). Below is the
          record as written.
        </p>
        <section
          aria-label="The record as written"
          className="rounded-panel border border-line bg-panel"
        >
          <CodeView name="record.json" text={JSON.stringify(raw, null, 2)} />
        </section>
      </div>
    </>
  );
}
