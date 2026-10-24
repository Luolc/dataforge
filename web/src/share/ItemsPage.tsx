// The route /<p>: a project's items in the home page's table, drawn with its
// parts: each item's ID with its revision, then the export's columns, each
// sorted by its heading, with a search of the IDs, the chips of each column
// that filters, and a refresh. A row opens its item wherever it is clicked.
// The export holds only the items it shows, so there is no status.
import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  type SortFn,
  type SortingState,
  tableFeatures,
  type Updater,
  useTable,
} from "@tanstack/react-table";
import { RefreshCw, Search } from "lucide-react";
import { Tooltip } from "radix-ui";
import { use, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { useLoaded } from "../api/client";
import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import { control } from "../ui/chip";
import { counted, time } from "../ui/format";
import { Sideways } from "../ui/sideways";
import {
  columnParam,
  columnSpec,
  compareValues,
  joined,
  parseSort,
  ROW_CLASS,
  type Spec,
  useOpenRow,
  type Value,
} from "../views/home/columns";
import { ColumnFilter, SortHeading } from "../views/home/ColumnControls";
import { byColumns, columnChips } from "../views/home/items";
import {
  itemHref,
  type Listed,
  listedItems,
  read,
  type ShareProject,
  shownId,
  SignedContext,
  useShareProject,
  useShareSource,
} from "./api";

/** What the list calls an item. */
const UNIT = "item";

const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
});
const helper = createColumnHelper<typeof features, Listed>();

// sortUndefined places undefined before this is called.
const compare: SortFn<typeof features, Listed> = (a, b, id) =>
  compareValues(a.getValue<Value>(id), b.getValue<Value>(id));

const valueOf = (item: Listed, index: number): unknown => item.values?.[index];

export function ItemsPage() {
  const { project: id } = useParams();
  const project = useShareProject(id);
  const name = project.state === "ok" ? project.data.name : (id ?? "");
  return (
    <Frame crumbs={[{ label: name }]}>
      {project.state === "ok" ? (
        <Items key={project.data.id} project={project.data} />
      ) : (
        <Note>{project.state === "error" ? project.message : "Loading…"}</Note>
      )}
    </Frame>
  );
}

function Items({ project }: { project: ShareProject }) {
  const { refused } = use(SignedContext);
  const source = useShareSource();
  // A refresh reads again, and the last read stays shown until it lands.
  const [generation, setGeneration] = useState(0);
  const loaded = useLoaded(
    project.id,
    async (signal) => ({
      items: await read(source.items(project.id), listedItems, refused, signal),
      at: new Date().toISOString(),
    }),
    generation,
    { limit: true },
  );
  if (loaded.state !== "ok")
    return (
      <Note>{loaded.state === "error" ? loaded.message : "Loading…"}</Note>
    );
  return (
    <ItemTable
      project={project}
      items={loaded.data.items}
      at={loaded.data.at}
      error={loaded.failed?.message ?? null}
      onRefresh={() => {
        setGeneration((g) => g + 1);
      }}
    />
  );
}

function ItemTable({
  project,
  items,
  at,
  error,
  onRefresh,
}: {
  project: ShareProject;
  items: Listed[];
  at: string;
  error: string | null;
  onRefresh: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const open = useOpenRow();
  const q = params.get("q") ?? "";
  const specs = useMemo((): Spec<Listed>[] => {
    const id: Spec<Listed> = {
      id: "item",
      title: "ID",
      numeric: false,
      value: (item) => shownId(item),
      cell: (item) => (
        <Link
          to={itemHref(project.id, item.id)}
          className="inline-flex target items-center font-mono text-xs break-words underline decoration-faint underline-offset-3"
        >
          {shownId(item)}
        </Link>
      ),
    };
    return [
      id,
      ...project.columns.map((column, index) =>
        columnSpec(column, index, (item: Listed) => ({
          value: valueOf(item, index),
          read: item.reads?.[index] ?? null,
          queued: null,
        })),
      ),
    ];
  }, [project]);
  // ?column-<index>=<value> for each column the page filters by.
  const picked = useMemo(() => {
    const found = new Map<number, string>();
    project.columns.forEach((c, index) => {
      const key = c.filter === true ? params.get(columnParam(index)) : null;
      if (key !== null) found.set(index, key);
    });
    return found;
  }, [project.columns, params]);
  const needle = q.trim().toLowerCase();
  const searched = items.filter((item) =>
    shownId(item).toLowerCase().includes(needle),
  );
  const shown = searched.filter((item) => byColumns(item, valueOf, picked));
  const chips = columnChips(project.columns, searched, valueOf, picked);
  const columns = useMemo(
    () =>
      helper.columns(
        specs.map((s) =>
          helper.accessor((item) => s.value(item), {
            id: s.id,
            header: s.title,
            sortUndefined: "last",
            sortFn: compare,
          }),
        ),
      ),
    [specs],
  );
  const sortParam = params.get("sort");
  const sorting = useMemo(
    () =>
      parseSort(
        sortParam,
        specs.map((s) => s.id),
      ),
    [sortParam, specs],
  );
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
  const onSortingChange = (updater: Updater<SortingState>) => {
    const next = typeof updater === "function" ? updater(sorting) : updater;
    const first = next[0];
    update((p) => {
      if (first) p.set("sort", `${first.desc ? "-" : ""}${first.id}`);
      else p.delete("sort");
    });
  };
  const table = useTable({
    features,
    columns,
    data: shown,
    getRowId: (item) => item.id,
    state: { sorting },
    onSortingChange,
    enableMultiSort: false,
  });
  const rows = table.getRowModel().rows;
  const hidden = items.length - shown.length;
  // What hides items, as the line under the filters names it.
  const filters = [
    ...(needle !== "" ? ["the search"] : []),
    ...[...picked.keys()].map(
      (index) => `the ${project.columns[index]?.title ?? ""} filter`,
    ),
  ];
  return (
    <Tooltip.Provider delayDuration={200}>
      <div className="flex flex-col gap-3 px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          {chips.map((c) => {
            const column = project.columns[c.index];
            if (column === undefined) return null;
            return (
              <ColumnFilter
                key={c.index}
                column={column}
                chips={c}
                picked={picked.get(c.index) ?? null}
                onPick={(key) => {
                  update((p) => {
                    if (key === null) p.delete(columnParam(c.index));
                    else p.set(columnParam(c.index), key);
                  });
                }}
                unit={UNIT}
              />
            );
          })}
          <label className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 focus-within:border-fg-3">
            <Search className="size-3.5 text-icon" aria-hidden />
            <input
              type="search"
              aria-label="Search IDs"
              placeholder="Search IDs"
              value={q}
              onChange={(e) => {
                const value = e.target.value;
                update((p) => {
                  if (value !== "") p.set("q", value);
                  else p.delete("q");
                });
              }}
              className="h-6 w-48 bg-transparent text-xs outline-none placeholder:text-fg-3"
            />
          </label>
          <div className="ml-auto flex items-center gap-2 text-xs text-fg-3">
            {error !== null && <span className="text-bad">{error}</span>}
            <span aria-live="polite">{`Read ${time(at)}`}</span>
            <button type="button" onClick={onRefresh} className={control}>
              <RefreshCw className="size-3.5 text-icon" aria-hidden />
              Refresh
            </button>
          </div>
        </div>
        {hidden > 0 && (
          <p className="flex items-center gap-2 text-xs text-fg-3">
            {hidden} of {counted(items.length, UNIT)} hidden by{" "}
            {joined(filters)}.
            <button
              type="button"
              className={control}
              onClick={() => {
                update((p) => {
                  p.delete("q");
                  for (const index of picked.keys())
                    p.delete(columnParam(index));
                });
              }}
            >
              Show all
            </button>
          </p>
        )}
        {/* The table scrolls sideways on its own, under the filters. */}
        <Sideways label="the table">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line bg-head text-xs text-fg-2">
                {table.getHeaderGroups()[0]?.headers.map((header, i) => {
                  const spec = specs[i];
                  return (
                    <SortHeading
                      key={header.id}
                      spec={spec}
                      sorted={header.column.getIsSorted()}
                      onSort={header.column.getToggleSortingHandler()}
                      className={
                        spec?.id === "item"
                          ? "sticky left-0 z-1 bg-head shadow-stuck"
                          : ""
                      }
                    />
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const item = row.original;
                return (
                  <tr
                    key={item.id}
                    className={ROW_CLASS}
                    onClick={(e) => {
                      open(e, itemHref(project.id, item.id));
                    }}
                  >
                    {specs.map((s) => (
                      <td
                        key={s.id}
                        className={`px-3 py-1.5 align-top ${s.numeric ? "text-right whitespace-nowrap tabular-nums" : ""} ${s.id === "item" ? "sticky left-0 z-1 bg-panel whitespace-nowrap shadow-stuck group-hover/row:bg-hover" : ""}`}
                      >
                        {s.cell(item)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Sideways>
        {rows.length === 0 && (
          <p className="py-6 text-center text-fg-3">
            {items.length === 0
              ? "The project has no items."
              : `Nothing matches ${joined(filters)}.`}
          </p>
        )}
      </div>
    </Tooltip.Provider>
  );
}
