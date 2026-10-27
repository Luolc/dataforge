// A project's own tab of the attempt page: Markdown files found in one or
// more directories of the attempt, its versions, one shown at a time, with
// the values the project names read from JSON files beside each. A tab of
// two or more versions, or of two or more files, its parts, shows one at a
// time, chosen from a column beside it.
import { ChevronRight, CircleAlert, CirclePause, FileText } from "lucide-react";
import { Fragment } from "react";
import { Link, useSearchParams } from "react-router";

import { type Loaded, useLoaded, useRefresh } from "../../api/client";
import type {
  Project,
  ProjectTab,
  Queued,
  TabPart,
  TabVersion,
  TabVersions,
} from "../../api/types";
import { Card, Note } from "../../ui/card";
import { TabBadge } from "../../ui/count";
import { counted, readValue } from "../../ui/format";
import { SideColumn, sideEntry } from "../../ui/side-column";
import { NOT_RUN } from "../../ui/status";
import { ToneLabel } from "../../ui/status-mark";
import { attemptHref } from "../attempt/href";
import { fetchFile, size } from "../files/file";
import { jobLabel, jobTimes, pausedLine } from "../queue/status";
import { ReportMarkdown } from "../report/markdown";
import { entryOf, versionGroups, versionTitle } from "./tree";
import { queuedOf, versionsOf } from "./versions";

/** A tab's count: its versions, Not run with none; nothing while loading. */
export function TabCount({
  tab,
  loaded,
}: {
  tab: ProjectTab;
  loaded: Loaded<TabVersions[]>;
}) {
  if (loaded.state === "loading")
    return <TabBadge label="Reading the versions">{null}</TabBadge>;
  if (loaded.state === "error") {
    const label = `Could not read the versions: ${loaded.message}`;
    return (
      <TabBadge label={label}>
        <CircleAlert
          role="img"
          aria-label={label}
          className="inline size-3 text-bad"
        />
      </TabBadge>
    );
  }
  const count = versionsOf(loaded, tab).length;
  return count === 0 ? (
    <TabBadge label={NOT_RUN} />
  ) : (
    <TabBadge label={counted(count, "version")}>{count}</TabBadge>
  );
}

/**
 * A tab's view: one version, the one the page's `version` names, else the
 * first, and of it the part named, else the first. A tab of two or more
 * versions lists them in a column beside it, grouped (versionGroups), with
 * the parts of the one shown under its entry; a version that others lie in
 * opens each of them from a row at its top.
 */
export function TabView({
  project,
  attempt,
  tab,
  part: named,
  loaded,
}: {
  project: Project;
  attempt: string;
  tab: ProjectTab;
  part: string | null;
  loaded: Loaded<TabVersions[]>;
}) {
  const [query] = useSearchParams();
  if (loaded.state === "loading") return <Note>Loading…</Note>;
  if (loaded.state === "error") return <Note>{loaded.message}</Note>;
  const versions = versionsOf(loaded, tab);
  const shown =
    versions.find((v) => v.dir === query.get("version")) ?? versions[0];
  if (shown === undefined)
    return <NoVersion tab={tab} queued={queuedOf(loaded, tab)} />;
  const { parts } = tab.versions[shown.source] ?? tab.versions[0];
  const part = parts.find((p) => p.name === named) ?? parts[0];
  const href = (version: string, name?: string) =>
    attemptHref(project.id, attempt, {
      tab: tab.name,
      version,
      ...(name === undefined ? {} : { part: name }),
    });
  const groups = versionGroups(versions, tab);
  const under = entryOf(groups, shown)?.under ?? [];
  const title = versionTitle(shown, tab);
  const view = (
    <div className="flex min-w-0 flex-col gap-3">
      <p className="flex flex-wrap items-baseline gap-2">
        <span className="text-fg-3">Version</span>
        <span className="font-medium">{title}</span>
        {!title.includes(shown.dir) && (
          <code className="font-mono text-xs text-fg-3">{shown.dir}</code>
        )}
      </p>
      {under.length > 0 && (
        <nav
          aria-label={`Versions under ${title}`}
          className="flex flex-wrap items-center gap-2"
        >
          <span className="text-fg-3">Under this version</span>
          {under.map((e) => (
            <Link
              key={e.version.dir}
              to={href(e.version.dir, named ?? undefined)}
              className="flex target items-center gap-1.5 rounded-row border border-line px-2.5 font-medium hover:bg-hover"
            >
              {e.title}
              <ChevronRight className="size-3 text-icon" aria-hidden />
            </Link>
          ))}
        </nav>
      )}
      {part.fields.length > 0 && <Fields part={part} version={shown} />}
      <TabMarkdown
        key={`${shown.dir}/${part.name}`}
        project={project}
        attempt={attempt}
        part={part}
        version={shown}
      />
    </div>
  );
  const partEntries = (depth: number) =>
    parts.length > 1
      ? parts.map((p) => (
          <Link
            key={`part-${p.name}`}
            to={href(shown.dir, p.name)}
            aria-current={p === part ? "page" : undefined}
            className={`${sideEntry} ${indent(depth)}`}
          >
            <FileText className="size-4 flex-none" aria-hidden />
            {p.title}
          </Link>
        ))
      : [];
  if (versions.length > 1)
    return (
      <div className="px-7 pt-5 pb-12">
        <SideColumn
          label={`${tab.title} versions`}
          entries={groups.map((g) => (
            <div
              key={g.key}
              role="group"
              aria-label={g.heading ?? undefined}
              className="flex flex-col gap-0.5 not-first:pt-2"
            >
              {g.heading !== null && (
                <p className="px-2 py-1 text-xs font-medium text-fg-3">
                  {g.heading}
                </p>
              )}
              {g.entries.map((e) => (
                <Fragment key={e.version.dir}>
                  <Link
                    to={href(e.version.dir, named ?? undefined)}
                    aria-current={
                      e.version !== shown
                        ? undefined
                        : parts.length > 1
                          ? "true"
                          : "page"
                    }
                    className={`${sideEntry} ${indent(e.depth)} aria-[current=true]:text-fg`}
                  >
                    <span className="wrap-anywhere">{e.title}</span>
                  </Link>
                  {e.version === shown && partEntries(e.depth + 1)}
                </Fragment>
              ))}
            </div>
          ))}
        >
          {view}
        </SideColumn>
      </div>
    );
  return (
    <div className="px-7 pt-5 pb-12">
      {parts.length > 1 ? (
        <SideColumn label={tab.title} entries={partEntries(0)}>
          {view}
        </SideColumn>
      ) : (
        view
      )}
    </div>
  );
}

/** How far an entry of the column stands in, by its depth. */
const indent = (depth: number) =>
  depth === 0 ? "" : depth === 1 ? "pl-6" : depth === 2 ? "pl-10" : "pl-14";

/**
 * A tab with no version: that the attempt has none yet, and, for a tab that
 * names its job, how the newest job of it stands and whether its queue is
 * paused.
 */
function NoVersion({
  tab,
  queued,
}: {
  tab: ProjectTab;
  queued: Queued | null;
}) {
  const job = queued?.job ?? null;
  return (
    <div className="flex flex-col gap-2 px-7 pt-5 pb-12">
      <p className="text-fg-3">{`${NOT_RUN}: this attempt has no ${tab.title} yet.`}</p>
      {queued !== null &&
        (queued.reason !== null ? (
          <p className="text-fg-3">{`How its job stands is not known: ${queued.reason}.`}</p>
        ) : job === null ? (
          <p className="text-fg-3">No job for it has been queued.</p>
        ) : (
          <p className="flex flex-wrap items-center gap-2">
            <Link
              to="/queue"
              className="inline-flex target items-center link hover:underline"
            >
              {`Job ${job.id.toString()}`}
            </Link>
            <ToneLabel {...jobLabel(job)} />
            <span className="text-fg-3">{jobTimes(job)}</span>
          </p>
        ))}
      {queued?.paused != null && (
        <p className="flex items-center gap-1.5 text-fg-3">
          <CirclePause
            className="size-icon-sm flex-none text-icon"
            aria-hidden
          />
          {pausedLine(queued.paused)}
        </p>
      )}
    </div>
  );
}

/**
 * Why a value or a file of a version is not there: Not run, then, for a file
 * that is not there, its path in the version's directory, else the reason as
 * read.
 */
function notRun(reason: string, version: TabVersion): string {
  const missing = /^(.+): No such file or directory$/.exec(reason)?.[1];
  if (missing === undefined) return `${NOT_RUN}: ${reason}`;
  const prefix = `${version.dir}/`;
  const path = missing.startsWith(prefix)
    ? missing.slice(prefix.length)
    : missing;
  return `${NOT_RUN}: no ${path} was written`;
}

/** Each field's title and value, or why it has none. */
function Fields({ part, version }: { part: TabPart; version: TabVersion }) {
  const read = version.parts.find((p) => p.name === part.name)?.fields ?? [];
  return (
    <section
      aria-label={`${part.title} fields`}
      className="rounded-panel border border-line bg-card px-5 py-4"
    >
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-1.5">
        {part.fields.map((field, i) => {
          const found = read[i];
          return (
            // The project's fields, fixed for the page: two may share a title.
            // eslint-disable-next-line @eslint-react/no-array-index-key
            <div key={i} className="contents">
              <dt className="text-fg-3">{field.title}</dt>
              <dd className="wrap-anywhere">
                {found?.read === "found" ? (
                  <FieldValue value={found.value} field={field} />
                ) : (
                  <span className="text-fg-3">
                    {found?.read === "not run"
                      ? notRun(found.reason ?? "", version)
                      : (found?.reason ?? "—")}
                  </span>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

/**
 * A field's value as read: a list of two or more strings, numbers and
 * booleans as a list, an item a line, else as readValue shows it.
 */
function FieldValue({
  value,
  field,
}: {
  value: unknown;
  field: TabPart["fields"][number];
}) {
  if (
    Array.isArray(value) &&
    value.length > 1 &&
    value.every((v) => ["string", "number", "boolean"].includes(typeof v))
  )
    return (
      <ul className="flex flex-col gap-0.5">
        {value.map((item, i) => (
          // The items of a list read from a file: two may be the same.
          // eslint-disable-next-line @eslint-react/no-array-index-key
          <li key={i}>{readValue("found", item, field)}</li>
        ))}
      </ul>
    );
  return readValue("found", value, field);
}

/** The version's Markdown, or why it is not shown. */
function TabMarkdown({
  project,
  attempt,
  part,
  version,
}: {
  project: Project;
  attempt: string;
  part: TabPart;
  version: TabVersion;
}) {
  const refresh = useRefresh();
  const path = `${version.dir}/${part.file}`;
  const loaded = useLoaded(
    JSON.stringify([project.id, attempt, path]),
    (signal) => fetchFile(project.id, attempt, path, signal),
    refresh,
  );
  const content = loaded.state === "ok" ? loaded.data : null;
  let body;
  if (loaded.state === "loading") body = "Loading…";
  else if (loaded.state === "error") body = loaded.message;
  else if (loaded.data.kind === "missing")
    body = notRun(loaded.data.reason, version);
  else if (loaded.data.kind === "too-large")
    body = `Not shown: ${loaded.data.reason}`;
  else if (loaded.data.kind === "binary")
    body = `Not shown: not UTF-8 text, ${size(loaded.data.size)}.`;
  return (
    <Card
      title={part.title}
      aside={
        <>
          <code className="font-mono">{path}</code>
          {content?.kind === "text" && ` · ${size(content.size)}`}
        </>
      }
    >
      {content?.kind === "text" ? (
        <ReportMarkdown project={project} text={content.text} />
      ) : (
        <p className="text-fg-3">{body}</p>
      )}
    </Card>
  );
}
