// An attempt: its header, a row of tabs (Runs, then one per deliverable kind
// in the order its first deliverable is declared) and the current tab. While
// the attempt runs, the page reads it again every POLL_MS.
import { type ReactNode, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Link } from "react-router";

import {
  type Failed,
  getJson,
  nextGeneration,
  RefreshContext,
  urls,
  useJson,
  useLoaded,
} from "../../api/client";
import { usePolling } from "../../api/poll";
import { z } from "zod";

import {
  type AttemptRecord,
  currentOf,
  deliverablesOf,
  type LiveInfo,
  liveInfo,
  type Project,
  readRecord,
} from "../../api/types";
import { type Crumb, Frame } from "../../shell/Shell";
import { useProject } from "../../shell/projects";
import { Note } from "../../ui/card";
import { CodeView } from "../../ui/code";
import { time } from "../../ui/format";
import { RunningIcon } from "../../ui/running";
import {
  attemptStatus,
  liveStatus,
  stateTitle,
  toneGlyph,
  toneText,
} from "../../ui/status";
import { StatusIcon, ToneIcon } from "../../ui/status-mark";
import { KindCount, KindTab } from "../registry";
import { RunsView } from "../runs/RunsView";
import { declaredMissing, tabKinds } from "./declared";
import { attemptHref } from "./href";

export function AttemptPage() {
  const { project: id } = useParams();
  const [query] = useSearchParams();
  const attempt = query.get("id");
  const project = useProject(id);
  // Raised by each read again; what is open, chosen or panned on the page
  // stays, since what was read before stays shown until the new read lands.
  const [refresh, setRefresh] = useState(nextGeneration);
  const url =
    id !== undefined && attempt !== null ? urls.record(id, attempt) : null;
  const loaded = useLoaded(
    url,
    (signal) => getJson(url ?? "", z.unknown(), signal).then(readRecord),
    refresh,
  );
  // How the attempt stands now; a page that cannot read it shows the record
  // alone, as for a project with no lock.
  const liveUrl =
    id !== undefined && attempt !== null ? urls.live(id, attempt) : null;
  const liveLoaded = useJson(liveUrl, liveInfo, refresh);
  const live = liveLoaded.state === "ok" ? liveLoaded.data : null;
  // Read again only while /live says it runs and the record has not ended.
  usePolling(
    live?.live === "running" &&
      project.state === "ok" &&
      loaded.state === "ok" &&
      loaded.data.shape === "record" &&
      attemptStatus(project.data, loaded.data.record) === null,
    () => {
      setRefresh(nextGeneration());
    },
  );
  // Said only after two failed reads in a row, so that one read of a record
  // caught half written stays quiet.
  const failed =
    [loaded, liveLoaded]
      .map((l) => (l.state === "ok" ? l.failed : undefined))
      .find((f) => f !== undefined && f.times >= 2) ?? null;
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
  else if (
    project.state === "loading" ||
    loaded.state === "loading" ||
    liveLoaded.state === "loading"
  )
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
        live={live}
        failed={failed}
        tab={tab}
        run={run}
        discarded={discarded}
      />
    );
  return (
    <Frame crumbs={crumbs}>
      <RefreshContext value={refresh}>{body}</RefreshContext>
    </Frame>
  );
}

function runOf(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) return null;
  return Number(value);
}

function Attempt({
  project,
  attempt,
  record,
  live: read,
  failed,
  tab,
  run,
  discarded,
}: {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  live: LiveInfo | null;
  /** The reads again that failed, when they have; null else. */
  failed: Failed | null;
  tab: string;
  run: number | null;
  discarded: number | null;
}) {
  // A record that has ended wins over a /live read before it ended: the page
  // then shows the record alone.
  const live = attemptStatus(project, record) === null ? read : null;
  const recorded = deliverablesOf(record);
  const deliverables = [...recorded, ...declaredMissing(project, record)];
  const kinds = tabKinds(project, deliverables);
  const itemsOf = (kind: string) => deliverables.filter((d) => d.kind === kind);
  const current = currentOf(record, live);
  const runs = record.visits.length + (current && !current.recorded ? 1 : 0);
  const tabs = [
    { key: "runs", title: "Runs", count: <>{runs}</> },
    ...kinds.map((kind) => {
      const items = itemsOf(kind);
      const first = items[0];
      const counted = recorded.filter((d) => d.kind === kind);
      const one = counted[0];
      return {
        key: kind,
        title: first?.title ?? kind,
        count:
          one === undefined ? (
            "—"
          ) : (
            <KindCount
              kind={kind}
              project={project.id}
              attempt={attempt}
              item={one}
              items={counted}
            />
          ),
      };
    }),
  ];
  return (
    <>
      <Header attempt={attempt}>
        <AttemptIcon project={project} record={record} live={live} />
      </Header>
      {failed !== null && <ReadFailed failed={failed} />}
      <Tabs project={project} attempt={attempt} tabs={tabs} current={tab} />
      {tab === "runs" ? (
        <RunsView
          project={project}
          attempt={attempt}
          record={record}
          live={live}
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
          live={live}
        />
      ) : (
        <Note>This attempt has no deliverable of kind {tab}.</Note>
      )}
    </>
  );
}

/**
 * The attempt's status as an icon: turning while it runs, May have stopped
 * when /live says so, else its status word as the record has it.
 */
function AttemptIcon({
  project,
  record,
  live,
}: {
  project: Project;
  record: AttemptRecord;
  live: LiveInfo | null;
}) {
  if (live?.live === "running") return <RunningIcon />;
  if (live?.live === "may have stopped")
    return <ToneIcon {...liveStatus["may have stopped"]} />;
  return <StatusIcon project={project} word={attemptStatus(project, record)} />;
}

/** That the page could not read the attempt again, so what it shows is older. */
function ReadFailed({ failed }: { failed: Failed }) {
  const Glyph = toneGlyph.warn;
  return (
    <p
      role="status"
      className={`flex items-center gap-1.5 px-7 pt-1 text-xs ${toneText.warn}`}
    >
      <Glyph className="size-3.5 flex-none" aria-hidden />
      {`Could not read the attempt again (${failed.message}); what is shown was read at ${time(new Date(failed.read).toISOString())}.`}
    </p>
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
