// The route /<p>/<name>: a snapshot page, a card per step of the counts a
// dataset writes, a step's table and a row's files (design-viz.md section 5,
// "Snapshot page").
import { Search } from "lucide-react";
import { Tooltip } from "radix-ui";
import { type ReactNode, useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import {
  ApiError,
  FileSourceContext,
  getJson,
  pageFiles,
  urls,
  useLoaded,
} from "../../api/client";
import type { Page, Project } from "../../api/types";
import {
  firstPath,
  pagePath,
  projectCrumbs,
  snapshotPage,
} from "../../shell/pages";
import { useProject } from "../../shell/projects";
import { type Crumb, Frame } from "../../shell/Shell";
import { Card, Note } from "../../ui/card";
import { Chip, control } from "../../ui/chip";
import { counted, figure, formatValue, time } from "../../ui/format";
import { ToneLabel } from "../../ui/status-mark";
import { toneGlyph, toneText } from "../../ui/status";
import { Files } from "../files";
import {
  columnParam,
  filterRows,
  isNumeric,
  pageNumber,
  pageOf,
  statusWord,
} from "./table";
import {
  noSnapshot,
  type Row,
  snapshot as snapshotSchema,
  type Snapshot,
  type SnapshotColumn,
  type Step,
} from "./types";

/** What a read of the snapshot gives: the snapshot, or the file it waits for. */
type Read = { snapshot: Snapshot } | { missing: string | null };

export function SnapshotPage() {
  const { project: id, page: name } = useParams();
  const project = useProject(id);
  const page = project.state === "ok" ? snapshotPage(project.data, name) : null;
  const [query] = useSearchParams();
  const read = useLoaded(
    project.state === "ok" && page
      ? urls.snapshot(project.data.id, name ?? "")
      : null,
    async (signal): Promise<Read> => {
      try {
        return {
          snapshot: await getJson(
            urls.snapshot(id ?? "", name ?? ""),
            snapshotSchema,
            signal,
          ),
        };
      } catch (e) {
        const missing =
          e instanceof ApiError ? noSnapshot.safeParse(e.body) : null;
        if (missing?.success === true) return { missing: missing.data.file };
        throw e;
      }
    },
  );
  const stepName = query.get("step");
  const entry = query.get("entry");
  const snapshot =
    read.state === "ok" && "snapshot" in read.data ? read.data.snapshot : null;
  const step = snapshot?.steps.find(
    (s) => s.name === stepName && s.entries !== undefined,
  );
  const crumbs: Crumb[] = projectCrumbs(
    project,
    id,
    (p) => snapshotPage(p, name),
    step === undefined,
  );
  const here =
    project.state === "ok" && page ? pagePath(project.data.id, page) : "";
  if (step !== undefined) {
    const stepHref = `${here}?${new URLSearchParams({ step: step.name }).toString()}`;
    crumbs.push(
      entry === null
        ? { label: step.title }
        : { label: step.title, to: stepHref },
    );
    if (entry !== null) crumbs.push({ label: entry });
  }
  let body: ReactNode;
  if (project.state !== "ok")
    body = (
      <Note>{project.state === "error" ? project.message : "Loading…"}</Note>
    );
  else if (!page) body = <Note>No page here.</Note>;
  else if (read.state === "loading") body = <Note>Loading…</Note>;
  else if (read.state === "error")
    body = <Note>The snapshot cannot be read: {read.message}</Note>;
  else if ("missing" in read.data)
    body = <NoSnapshot file={read.data.missing} />;
  else if (step?.entries === undefined)
    body = (
      <Overview
        project={project.data}
        page={page}
        snapshot={read.data.snapshot}
      />
    );
  else if (entry === null)
    body = <StepTable snapshot={read.data.snapshot} step={step} />;
  else
    body = (
      <EntryView
        project={project.data}
        page={page}
        snapshot={read.data.snapshot}
        step={step}
        id={entry}
      />
    );
  return <Frame crumbs={crumbs}>{body}</Frame>;
}

/** Where the page stands before the dataset has written its snapshot. */
function NoSnapshot({ file }: { file: string | null }) {
  return (
    <div className="flex flex-col gap-2 p-6 text-fg-3">
      <p className="font-medium text-fg">No snapshot has been written yet.</p>
      {file !== null && (
        <p>
          The project file names it{" "}
          <code className="font-mono text-xs break-all text-fg">{file}</code>,
          and there is no file there.
        </p>
      )}
    </div>
  );
}

/** The page's title and when its snapshot was written, over a card a step. */
function Overview({
  project,
  page,
  snapshot,
}: {
  project: Project;
  page: Page;
  snapshot: Snapshot;
}) {
  return (
    <div className="flex flex-col gap-4 px-6 py-5">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg font-semibold">{page.title}</h1>
        <p className="text-xs text-fg-3">
          Snapshot of {time(snapshot.generated_at)}
        </p>
      </header>
      <ul aria-label="Steps" className="flex flex-wrap gap-3">
        {snapshot.steps.map((step) => (
          <li key={step.name} className="flex w-step-card flex-none">
            <StepCard project={project} page={page} step={step} />
          </li>
        ))}
      </ul>
    </div>
  );
}

const cardClass =
  "flex w-full flex-col gap-2 rounded-panel border border-line bg-card px-4 py-3.5";

/** A step's card, a link to its table or to the page it names. */
function StepCard({
  project,
  page,
  step,
}: {
  project: Project;
  page: Page;
  step: Step;
}) {
  const of = step.of;
  const share = of === undefined ? null : Math.min(1, step.value / of);
  const content = (
    <>
      <h2 className="text-xs font-semibold tracking-wide text-fg-2 uppercase">
        {step.title}
      </h2>
      <p className="flex flex-wrap items-baseline gap-x-1.5 tabular-nums">
        <span className="text-figure font-semibold text-fg">
          {figure(step.value)}
        </span>
        {of !== undefined && (
          <span className="text-sm text-fg-2">/ {figure(of)}</span>
        )}
        {step.unit !== undefined && (
          <span className="text-sm text-fg-2">{step.unit}</span>
        )}
      </p>
      {share !== null && (
        <div
          aria-hidden
          className="h-meter overflow-hidden rounded-meter bg-line-subtle"
        >
          <div
            className="h-full rounded-meter bg-accent"
            style={{ width: `${(Math.max(0, share) * 100).toString()}%` }}
          />
        </div>
      )}
      {step.note !== undefined && (
        <p className="text-xs break-words text-fg-3">{step.note}</p>
      )}
      {step.generated_at !== undefined && (
        <p className="text-xs text-fg-3">Counted {time(step.generated_at)}</p>
      )}
    </>
  );
  const to =
    step.entries !== undefined
      ? `${pagePath(project.id, page)}?${new URLSearchParams({ step: step.name }).toString()}`
      : step.page !== undefined
        ? pageHref(project, step.page)
        : null;
  if (to === null) return <section className={cardClass}>{content}</section>;
  return (
    <Link to={to} className={`${cardClass} target hover:bg-hover`}>
      {content}
    </Link>
  );
}

/** The path of the project's page of a name, else its first page. */
function pageHref(project: Project, name: string): string {
  const page = project.pages?.find((p) => p.name === name);
  return page ? pagePath(project.id, page) : firstPath(project);
}

/** A value of a row as its column shows it. */
function Cell({
  snapshot,
  column,
  value,
}: {
  snapshot: Snapshot;
  column: SnapshotColumn;
  value: string | number | null | undefined;
}) {
  if (value === null || value === undefined) return <>—</>;
  if (column.format === "status" && typeof value === "string")
    return <ToneLabel {...statusWord(snapshot.statuses, value)} />;
  return <>{formatValue(value, { format: textFormat(column) })}</>;
}

const textFormat = (column: SnapshotColumn) =>
  column.format === undefined || column.format === "status"
    ? "text"
    : column.format;

/** A step's table: its rows, searched, filtered and 50 to a page. */
function StepTable({ snapshot, step }: { snapshot: Snapshot; step: Step }) {
  const entries = step.entries;
  const [params, setParams] = useSearchParams();
  if (entries === undefined) return null;
  const q = params.get("q") ?? "";
  const chosen = new Map<string, string>();
  for (const column of entries.columns) {
    const value =
      column.filter === true ? params.get(columnParam(column.name)) : null;
    if (value !== null) chosen.set(column.name, value);
  }
  const filtered = filterRows(entries, q, chosen);
  const { rows, page, pages, from } = pageOf(
    filtered.shown,
    pageNumber(params.get("n")),
  );
  const unit = entries.id_title;
  // A search or a filter starts the table at its first page again.
  const update = (change: (next: URLSearchParams) => void) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        change(next);
        return next;
      },
      { replace: true },
    );
  };
  const pick = (name: string, value: string | null) => {
    update((p) => {
      if (value === null) p.delete(columnParam(name));
      else p.set(columnParam(name), value);
      p.delete("n");
    });
  };
  const hiders = [
    ...(q !== "" ? ["the search"] : []),
    ...entries.columns
      .filter((c) => chosen.has(c.name))
      .map((c) => `the ${c.title} filter`),
  ];
  const at = (n: number) => {
    const next = new URLSearchParams(params);
    if (n === 1) next.delete("n");
    else next.set("n", n.toString());
    return `?${next.toString()}`;
  };
  return (
    <Tooltip.Provider delayDuration={200}>
      <div className="flex flex-col gap-3 px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          {filtered.chips.map(({ column, all, values }) => {
            const picked = chosen.get(column.name) ?? null;
            return (
              <div
                key={column.name}
                role="group"
                aria-label={`Filter by ${column.title}`}
                className="flex flex-wrap items-center gap-1.5"
              >
                <span aria-hidden className="text-xs text-fg-3">
                  {column.title}
                </span>
                <Chip
                  pressed={picked === null}
                  onClick={() => {
                    pick(column.name, null);
                  }}
                  label="All"
                  count={all}
                  hint={counted(all, "row")}
                />
                {values.map(({ value, count }) => {
                  const word =
                    column.format === "status"
                      ? statusWord(snapshot.statuses, value)
                      : null;
                  const Glyph = word ? toneGlyph[word.tone] : null;
                  return (
                    <Chip
                      key={value}
                      pressed={picked === value}
                      onClick={() => {
                        pick(column.name, picked === value ? null : value);
                      }}
                      icon={
                        Glyph && word ? (
                          <Glyph
                            className={`size-icon-sm ${toneText[word.tone]}`}
                            aria-hidden
                          />
                        ) : undefined
                      }
                      label={word?.label ?? value}
                      count={count}
                      hint={counted(count, "row")}
                    />
                  );
                })}
              </div>
            );
          })}
          <label className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 focus-within:border-fg-3">
            <Search className="size-3.5 text-icon" aria-hidden />
            <input
              type="search"
              aria-label={`Search ${unit} IDs`}
              placeholder={`Search ${unit} IDs`}
              value={q}
              onChange={(e) => {
                const value = e.target.value;
                update((p) => {
                  if (value !== "") p.set("q", value);
                  else p.delete("q");
                  p.delete("n");
                });
              }}
              className="h-6 w-48 bg-transparent text-xs outline-none placeholder:text-fg-3"
            />
          </label>
        </div>
        {filtered.hidden > 0 && (
          <p className="flex flex-wrap items-center gap-2 text-xs text-fg-3">
            {filtered.hidden} of {counted(entries.rows.length, "row")} hidden by{" "}
            {joined(hiders)}.
            <button
              type="button"
              className={control}
              onClick={() => {
                update((p) => {
                  p.delete("q");
                  p.delete("n");
                  for (const name of chosen.keys()) p.delete(columnParam(name));
                });
              }}
            >
              Show all
            </button>
          </p>
        )}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line bg-head text-xs text-fg-2">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  {entries.id_title}
                </th>
                {entries.columns.map((column) => (
                  <th
                    key={column.name}
                    scope="col"
                    className={`px-3 py-1.5 font-medium ${isNumeric(column) ? "text-right" : "text-left"}`}
                  >
                    {column.title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-line-subtle">
                  <th scope="row" className="px-3 py-1.5 text-left font-medium">
                    {row.dir ? (
                      <Link
                        to={`?${new URLSearchParams({ step: step.name, entry: row.id }).toString()}`}
                        className="inline-flex target items-center rounded-row font-mono text-xs wrap-anywhere link hover:underline"
                      >
                        {row.id}
                      </Link>
                    ) : (
                      <span className="font-mono text-xs wrap-anywhere">
                        {row.id}
                      </span>
                    )}
                  </th>
                  {entries.columns.map((column) => (
                    <td
                      key={column.name}
                      className={`px-3 py-1.5 ${isNumeric(column) ? "text-right tabular-nums" : "text-left"}`}
                    >
                      <Cell
                        snapshot={snapshot}
                        column={column}
                        value={row.values[column.name]}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filtered.shown.length === 0 ? (
          <p className="py-6 text-center text-fg-3">
            {entries.rows.length === 0
              ? "This step has no rows."
              : "Nothing matches the search and the filters."}
          </p>
        ) : (
          <nav
            aria-label="Pages of the table"
            className="flex flex-wrap items-center gap-2 text-xs text-fg-3"
          >
            <span className="tabular-nums">
              {`Rows ${figure(from + 1)}–${figure(from + rows.length)} of ${figure(filtered.shown.length)}`}
              {pages > 1 && ` · page ${figure(page)} of ${figure(pages)}`}
            </span>
            {pages > 1 && (
              <>
                <PageLink to={page > 1 ? at(page - 1) : null}>
                  Previous
                </PageLink>
                <PageLink to={page < pages ? at(page + 1) : null}>
                  Next
                </PageLink>
              </>
            )}
          </nav>
        )}
      </div>
    </Tooltip.Provider>
  );
}

/** Previous or Next, a link while there is such a page. */
function PageLink({ to, children }: { to: string | null; children: string }) {
  if (to === null)
    return (
      <span
        aria-disabled="true"
        className={`${control} text-fg-3 opacity-idle`}
      >
        {children}
      </span>
    );
  return (
    <Link to={to} className={control}>
      {children}
    </Link>
  );
}

function joined(words: string[]): string {
  return words.length <= 1
    ? (words[0] ?? "")
    : `${words.slice(0, -1).join(", ")} and ${words.at(-1) ?? ""}`;
}

/** A row's values over the files of its directory. */
function EntryView({
  project,
  page,
  snapshot,
  step,
  id,
}: {
  project: Project;
  page: Page;
  snapshot: Snapshot;
  step: Step;
  id: string;
}) {
  const source = useMemo(() => pageFiles(page.name ?? ""), [page.name]);
  const row: Row | undefined = step.entries?.rows.find((r) => r.id === id);
  if (row === undefined)
    return <Note>{`${step.title} has no row ${id}.`}</Note>;
  const columns = step.entries?.columns ?? [];
  const dir = row.dir;
  return (
    <div className="flex flex-col gap-4 px-6 py-5">
      <header className="flex flex-col gap-2">
        <h1 className="font-mono text-lg font-semibold wrap-anywhere">
          {row.id}
        </h1>
        {columns.length > 0 && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
            {columns.map((column) => (
              <div key={column.name} className="contents">
                <dt className="text-fg-3">{column.title}</dt>
                <dd className={isNumeric(column) ? "tabular-nums" : ""}>
                  <Cell
                    snapshot={snapshot}
                    column={column}
                    value={row.values[column.name]}
                  />
                </dd>
              </div>
            ))}
          </dl>
        )}
      </header>
      {dir === undefined ? (
        <Note>This row has no directory.</Note>
      ) : (
        <FileSourceContext value={source}>
          <Card title="Files" flush>
            <Files
              project={project.id}
              attempt={dir.root}
              root={dir.path}
              {...(dir.open !== undefined
                ? { first: `${dir.path}/${dir.open}` }
                : {})}
            />
          </Card>
        </FileSourceContext>
      )}
    </div>
  );
}
