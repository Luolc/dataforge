// An attempt: its header, a row of tabs (Runs, then one per deliverable kind
// in the order its first deliverable is declared, then the project's own) and
// the current tab. While the attempt runs, the page reads it again every
// POLL_MS.
import { type ReactNode, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router";

import {
  ApiError,
  type Failed,
  getJson,
  type Loaded,
  nextGeneration,
  RefreshContext,
  urls,
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
  type ReadRecord,
} from "../../api/types";
import { attemptsPage, projectCrumbs } from "../../shell/pages";
import { type Crumb, Frame } from "../../shell/Shell";
import { useProject } from "../../shell/projects";
import { Note } from "../../ui/card";
import { CodeView } from "../../ui/code";
import { TabBadge } from "../../ui/count";
import { counted, time } from "../../ui/format";
import { RunningIcon } from "../../ui/running";
import {
  attemptStatus,
  liveStatus,
  stateTitle,
  toneGlyph,
  toneText,
  viewerStatus,
  withRowStatus,
} from "../../ui/status";
import { StatusIcon, StatusLabel, ToneIcon } from "../../ui/status-mark";
import { declared, kindTitle, leading } from "../kind";
import { KindCount, KindTab } from "../registry";
import { ItemActions } from "./Actions";
import { RunsView } from "../runs/RunsView";
import { asShown, declaredMissing, tabKinds } from "./declared";
import { AttemptFacts } from "./facts";
import { HeaderExtras } from "./extras";
import { attemptHref } from "./href";
import { NewestContext } from "../newest";
import { newestOf, useNewestPaths, withNewest } from "./newest";
import { TabNav } from "./TabNav";
import { TabCount, TabView } from "../tabs";
import { useTabVersions } from "../tabs/versions";
import { WORKFLOW } from "../definition/details";
import { useSnapshot } from "../definition/load";

/**
 * What the page read of the attempt: its record file, or, for an attempt with
 * a row in the store and no record file, the row alone.
 */
type PageRead = ReadRecord | { shape: "none" };

export function AttemptPage() {
  const { project: id } = useParams();
  const [query] = useSearchParams();
  const attempt = query.get("id");
  const project = useProject(id);
  // A new generation for each read again; what is open, chosen or panned on
  // the page stays, since what was read before stays shown until the new
  // read lands.
  const [first] = useState(nextGeneration);
  const [refresh, setRefresh] = useState(first);
  const again = refresh !== first;
  const url =
    id !== undefined && attempt !== null ? urls.record(id, attempt) : null;
  const liveUrl =
    id !== undefined && attempt !== null ? urls.live(id, attempt) : null;
  // The record and how the attempt stands now (/live), read together so that
  // what the page shows of the two comes from one read.
  const both = useLoaded(
    url,
    async (signal) => {
      const [read, live] = await Promise.all([
        getJson(url ?? "", z.unknown(), signal).then(
          (raw): PageRead => readRecord(raw),
          // Only an absent file may leave the row to speak; /live says below
          // whether there is a row.
          (error: unknown): PageRead => {
            if (error instanceof ApiError && error.status === 404)
              return { shape: "none" };
            throw error;
          },
        ),
        // A page that cannot read /live at first shows the record alone, as
        // for a project with no lock; a read again keeps what it showed.
        getJson(liveUrl ?? "", liveInfo, signal).catch((error: unknown) => {
          if (again) throw error;
          return null;
        }),
      ]);
      // /live has no word for an attempt whose record it cannot read: a
      // record that has not ended makes that a failed read, not news.
      if (
        again &&
        live?.live == null &&
        read.shape === "record" &&
        project.state === "ok" &&
        attemptStatus(
          project.data,
          withRowStatus(project.data, read.record, live),
        ) === null
      )
        throw new Error("/live could not read the record");
      if (read.shape === "none" && live?.state == null)
        throw new ApiError("The attempt has no record file and no row.");
      return { read, live };
    },
    refresh,
    // Both reads end when their signal aborts.
    { limit: true },
  );
  const live = both.state === "ok" ? both.data.live : null;
  // With the project's runs, the record shows its row's status.
  const loaded: Loaded<PageRead> =
    both.state === "ok"
      ? {
          state: "ok",
          data:
            both.data.read.shape === "record" && project.state === "ok"
              ? {
                  ...both.data.read,
                  record: withRowStatus(
                    project.data,
                    both.data.read.record,
                    live,
                  ),
                }
              : both.data.read,
        }
      : both;
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
    both.state === "ok" && (both.failed?.times ?? 0) >= 2
      ? (both.failed ?? null)
      : null;
  const tab = query.get("tab") ?? "runs";
  const run = runOf(query.get("run"));
  const part = query.get("part");
  const discarded = runOf(query.get("discarded"));
  // A stage of the attempt's definition, or its workflow file, from the Runs
  // tab's column; it wins over a run, for a project that names a definition.
  const defn =
    tab === "runs" && project.state === "ok" && project.data.definition != null
      ? query.get("defn")
      : null;
  const summary =
    tab === "runs" && run === null && discarded === null && defn === null;
  const crumbs: Crumb[] = [
    ...projectCrumbs(project, id, attemptsPage, false),
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
  if (run !== null && defn === null && visit && project.state === "ok")
    crumbs.push({
      label: `Run ${String(run + 1)} · ${stateTitle(project.data, visit.state)}`,
    });
  if (defn !== null && project.state === "ok")
    crumbs.push({
      label:
        defn === WORKFLOW
          ? "Workflow file"
          : `${stateTitle(project.data, defn)} · prompts and rubrics`,
    });
  let body;
  if (attempt === null) body = <Note>No attempt is named.</Note>;
  else if (project.state === "error") body = <Note>{project.message}</Note>;
  else if (loaded.state === "error") body = <Note>{loaded.message}</Note>;
  else if (project.state === "loading" || loaded.state === "loading")
    body = <Note>Loading…</Note>;
  else if (loaded.data.shape === "none")
    body = <RowOnly project={project.data} attempt={attempt} live={live} />;
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
        defn={defn}
        part={part}
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
  record: recordRead,
  live: read,
  failed,
  tab: asked,
  run,
  discarded,
  defn,
  part,
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
  defn: string | null;
  /** The part of a project's tab shown, by name; null for its first. */
  part: string | null;
}) {
  const newest = useNewestPaths(project, attempt);
  const newestRead = useMemo(
    () => newestOf(project, newest),
    [project, newest],
  );
  const record = withNewest(project, asShown(project, recordRead), newest);
  // A record that has ended wins over a /live read before it ended: the page
  // then shows the record alone.
  const live = attemptStatus(project, record) === null ? read : null;
  const deliverables = [
    ...deliverablesOf(record),
    ...declaredMissing(project, record),
  ];
  const kinds = tabKinds(project, deliverables);
  const itemsOf = (kind: string) => deliverables.filter((d) => d.kind === kind);
  const current = currentOf(record, live);
  const runs = record.visits.length + (current && !current.recorded ? 1 : 0);
  const definition = useSnapshot(project, attempt);
  // A tab of the project's that shares its name with a kind of the record
  // gives way to it.
  const own = (project.tabs ?? []).filter((t) => !kinds.includes(t.name));
  const versions = useTabVersions(project, attempt);
  // A link made when a tab was keyed by a deliverable's name opens the tab
  // of that deliverable's kind.
  const named = deliverables.find((d) => d.name === asked);
  const tab =
    named !== undefined &&
    !kinds.includes(asked) &&
    !own.some((t) => t.name === asked) &&
    kinds.includes(named.kind)
      ? named.kind
      : asked;
  const ownTab = own.find((t) => t.name === tab);
  const tabs = [
    {
      key: "runs",
      title: "Runs",
      count: <TabBadge label={counted(runs, "run")}>{runs}</TabBadge>,
    },
    ...kinds.map((kind) => {
      const items = itemsOf(kind);
      const title = kindTitle(project, record, kind, items);
      const one = leading(project, record, items);
      return {
        key: kind,
        title,
        count: one && (
          <KindCount
            kind={kind}
            title={title}
            project={project.id}
            attempt={attempt}
            item={one}
            items={items}
            word={declared(project, one.name)?.count}
          />
        ),
      };
    }),
    ...own.map((t) => ({
      key: t.name,
      title: t.title,
      count: <TabCount tab={t} loaded={versions} />,
    })),
  ];
  return (
    <NewestContext value={newestRead}>
      <Header tags={<HeaderExtras project={project} attempt={attempt} />}>
        <AttemptIcon project={project} record={record} live={live} />
      </Header>
      <AttemptFacts project={project} record={record} live={live} />
      <Actions project={project} attempt={attempt} />
      <ReadFailed failed={failed} />
      <RowNote note={read?.note ?? null} />
      <Tabs project={project} attempt={attempt} tabs={tabs} current={tab} />
      {tab === "runs" ? (
        <RunsView
          project={project}
          attempt={attempt}
          record={record}
          live={live}
          run={run}
          discarded={discarded}
          definition={project.definition == null ? null : definition}
          defn={defn}
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
      ) : ownTab !== undefined ? (
        <TabView
          key={tab}
          project={project}
          attempt={attempt}
          tab={ownTab}
          part={part}
          loaded={versions}
        />
      ) : (
        <Note>This attempt has no deliverable of kind {tab}.</Note>
      )}
    </NewestContext>
  );
}

/**
 * The attempt's status as an icon: turning while it runs, May have stopped
 * when /live says so, Stopped when it also gives the job that ran it, ended,
 * else its status word as the record has it.
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
    return (
      <ToneIcon
        {...(live.stopped == null
          ? liveStatus["may have stopped"]
          : viewerStatus.stopped)}
      />
    );
  return <StatusIcon project={project} word={attemptStatus(project, record)} />;
}

/**
 * That the page could not read the attempt again, so what it shows is older;
 * the status region stays on the page, so that a reader announces the line
 * when it comes.
 */
function ReadFailed({ failed }: { failed: Failed | null }) {
  const Glyph = toneGlyph.warn;
  return (
    <div role="status">
      {failed !== null && (
        <p
          className={`flex items-center gap-1.5 px-7 pt-1 text-xs ${toneText.warn}`}
        >
          <Glyph className="size-3.5 flex-none" aria-hidden />
          {`Could not read the attempt again (${failed.message.replace(/\.$/, "")}); what is shown was read at ${time(new Date(failed.read).toISOString())}.`}
        </p>
      )}
    </div>
  );
}

/**
 * What a reconcile has yet to do with the attempt's row in the store, such as
 * a holder that is gone (design-viz.md section 4); nothing when there is none.
 */
function RowNote({ note }: { note: string | null }) {
  const Glyph = toneGlyph.warn;
  return note === null ? null : (
    <p
      className={`flex items-center gap-1.5 px-7 pt-1 text-xs ${toneText.warn}`}
    >
      <Glyph className="size-3.5 flex-none" aria-hidden />
      {`${note[0]?.toUpperCase() ?? ""}${note.slice(1)}.`}
    </p>
  );
}

/** The item's actions, when the project names any. */
function Actions({ project, attempt }: { project: Project; attempt: string }) {
  return (project.actions ?? []).length > 0 ? (
    <ItemActions project={project} attempt={attempt} />
  ) : null;
}

interface Tab {
  key: string;
  title: string;
  count?: ReactNode;
}

/** The page's heading; the attempt's ID is the last crumb above it. */
function Header({
  children,
  tags,
}: {
  children?: ReactNode;
  tags?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 px-7 pt-5">
      {children}
      <h1 className="text-xl font-semibold tracking-tight">Attempt</h1>
      {tags}
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
    <TabNav
      label="Attempt"
      tabs={tabs.map((t) => ({
        ...t,
        to: attemptHref(project.id, attempt, { tab: t.key }),
      }))}
      current={current}
    />
  );
}

function RowOnly({
  project,
  attempt,
  live,
}: {
  project: Project;
  attempt: string;
  live: LiveInfo | null;
}) {
  return (
    <>
      <Header />
      <Actions project={project} attempt={attempt} />
      <div className="flex flex-col gap-2 px-7 pt-5 pb-12">
        <p className="text-fg-2">
          This attempt has no record file. Its row in the store says:
        </p>
        <p>
          {live?.status != null ? (
            <StatusLabel project={project} word={live.status} />
          ) : (
            <span className="text-fg-2">{live?.state}</span>
          )}
          {live?.reason != null && (
            <span className="text-fg-2"> — {live.reason}</span>
          )}
        </p>
        {live?.note != null && <p className="text-fg-3">{live.note}</p>}
      </div>
    </>
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
      <Header />
      <Actions project={project} attempt={attempt} />
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
