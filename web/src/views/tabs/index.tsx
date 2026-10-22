// A project's own tab of the attempt page: Markdown files found in one or
// more directories of the attempt, its versions, one shown at a time, with
// the values the project names read from JSON files beside each. A tab of
// two or more files, its parts, shows one at a time, chosen from a column
// beside it.
import { CircleAlert, FileText } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router";

import { type Loaded, useLoaded, useRefresh } from "../../api/client";
import type {
  Project,
  ProjectTab,
  TabPart,
  TabVersion,
  TabVersions,
} from "../../api/types";
import { Card, Note } from "../../ui/card";
import { readValue } from "../../ui/format";
import { ChoiceMenu } from "../../ui/menu";
import { SideColumn, sideEntry } from "../../ui/side-column";
import { NOT_RUN } from "../../ui/status";
import { attemptHref } from "../attempt/href";
import { fetchFile, size } from "../files/file";
import { ReportMarkdown } from "../report/markdown";
import { versionsOf } from "./versions";

/** A tab's count: its versions, Not run with none; nothing while loading. */
export function TabCount({
  tab,
  loaded,
}: {
  tab: ProjectTab;
  loaded: Loaded<TabVersions[]>;
}) {
  if (loaded.state === "loading") return null;
  if (loaded.state === "error")
    return (
      <CircleAlert
        role="img"
        aria-label={`Could not read the versions: ${loaded.message}`}
        className="inline size-3 text-bad"
      />
    );
  const count = versionsOf(loaded, tab).length;
  return <>{count === 0 ? NOT_RUN : count}</>;
}

/**
 * A tab's view: one version, the first unless another is chosen, and of it
 * the part named, else the first; the version chosen stays as another part
 * is shown.
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
  const [chosen, setChosen] = useState<string>();
  if (loaded.state === "loading") return <Note>Loading…</Note>;
  if (loaded.state === "error") return <Note>{loaded.message}</Note>;
  const versions = versionsOf(loaded, tab);
  const shown = versions.find((v) => v.dir === chosen) ?? versions[0];
  if (shown === undefined)
    return (
      <Note>
        {`${NOT_RUN}: this attempt has no ${tab.title} yet (no directory matching ${tab.versions.join(" or ")}).`}
      </Note>
    );
  const part = tab.parts.find((p) => p.name === named) ?? tab.parts[0];
  const choices = versions.map((v) => ({ key: v.dir, title: v.dir }));
  const view = (
    <>
      {part.fields.length > 0 && <Fields part={part} version={shown} />}
      <TabMarkdown
        key={`${shown.dir}/${part.name}`}
        project={project}
        attempt={attempt}
        part={part}
        version={shown}
      />
    </>
  );
  return (
    <div className="flex flex-col gap-3 px-7 pt-5 pb-12">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-fg-3">Version</span>
        {versions.length > 1 ? (
          <ChoiceMenu
            label="Version"
            items={choices}
            chosen={{ key: shown.dir, title: shown.dir }}
            onChoose={setChosen}
          />
        ) : (
          <code className="font-mono">{shown.dir}</code>
        )}
        <span className="text-fg-3">
          {`${(versions.indexOf(shown) + 1).toString()} of ${versions.length.toString()}`}
        </span>
      </div>
      {tab.parts.length > 1 ? (
        <Parts project={project} attempt={attempt} tab={tab} shown={part}>
          {view}
        </Parts>
      ) : (
        view
      )}
    </div>
  );
}

/** A tab's parts in a column, as the Runs tab's, beside the one shown. */
function Parts({
  project,
  attempt,
  tab,
  shown,
  children,
}: {
  project: Project;
  attempt: string;
  tab: ProjectTab;
  shown: TabPart;
  children: ReactNode;
}) {
  return (
    <SideColumn
      label={tab.title}
      entries={tab.parts.map((p) => (
        <Link
          key={p.name}
          to={attemptHref(project.id, attempt, { tab: tab.name, part: p.name })}
          aria-current={p === shown ? "page" : undefined}
          className={sideEntry}
        >
          <FileText className="size-4 flex-none" aria-hidden />
          {p.title}
        </Link>
      ))}
    >
      <div className="flex min-w-0 flex-col gap-3">{children}</div>
    </SideColumn>
  );
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
                  readValue(found.read, found.value, field.format)
                ) : (
                  <span className="text-fg-3">
                    {found?.read === "not run"
                      ? `${NOT_RUN}: ${found.reason ?? ""}`
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
    body = `${NOT_RUN}: ${loaded.data.reason}`;
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
