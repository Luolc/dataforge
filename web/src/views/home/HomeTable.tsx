import {
  createColumnHelper,
  createSortedRowModel,
  rowExpandingFeature,
  rowSortingFeature,
  type SortFn,
  type SortingState,
  tableFeatures,
  type Updater,
  useTable,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Search,
} from "lucide-react";
import { Tooltip } from "radix-ui";
import {
  type MouseEvent,
  type ReactNode,
  useId,
  useMemo,
  useState,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import type { AttemptRow, Column, Project } from "../../api/types";
import { duration, time, usd } from "../../ui/format";
import {
  StatusLabel,
  stateTitle,
  statusOf,
  toneGlyph,
  toneText,
} from "../../ui/status";
import { attemptHref } from "../attempt/href";
import type { Read } from "./index";
import {
  filterItems,
  flagged,
  GROUPS,
  type Item,
  keyWord,
  rowSeconds,
  rowStatus,
  spent,
  toItems,
  withHomeStatuses,
} from "./items";

/** A row of the table: an item, standing for its newest attempt, or one of its attempts. */
interface Line {
  item: Item;
  row: AttemptRow;
  sub: boolean;
}

type Value = number | string | undefined;

/** A column: its heading, the value it sorts by and its cell. */
interface Spec {
  id: string;
  title: string;
  numeric: boolean;
  value: (line: Line) => Value;
  cell: (line: Line) => ReactNode;
}

const features = tableFeatures({
  rowSortingFeature,
  rowExpandingFeature,
  sortedRowModel: createSortedRowModel(),
});
const helper = createColumnHelper<typeof features, Item>();

// Values of one column are all numbers or all strings; undefined is placed
// by sortUndefined before this is called.
const compare: SortFn<typeof features, Item> = (a, b, id) => {
  const x = a.getValue<Value>(id);
  const y = b.getValue<Value>(id);
  if (typeof x === "number" && typeof y === "number") return x - y;
  return String(x).localeCompare(String(y), undefined, { numeric: true });
};

const itemLine = (item: Item): Line => ({
  item,
  row: item.newest,
  sub: false,
});

const timeValue = (iso: string | null): Value => {
  const t = iso === null ? NaN : Date.parse(iso);
  return Number.isNaN(t) ? undefined : t;
};

const NUMERIC_FORMATS = new Set(["number", "usd", "duration", "time"]);

/** A project column's value as its format says; "—" when there is none. */
function formatValue(value: unknown, format: Column["format"]): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (format === "usd") return usd(value);
    if (format === "duration") return duration(value);
  }
  if (typeof value === "string") return format === "time" ? time(value) : value;
  return typeof value === "number" ? String(value) : JSON.stringify(value);
}

function specs(project: Project): Spec[] {
  const fixed: Spec[] = [
    {
      id: "item",
      title: project.item.title,
      numeric: false,
      value: (l) => l.item.id,
      cell: () => null, // drawn by ItemCell, which needs the row's state
    },
    {
      id: "status",
      title: "Status",
      numeric: false,
      value: (l) => statusOf(project, rowStatus(l.row)).label,
      cell: (l) => <StatusCell project={project} row={l.row} />,
    },
    {
      id: "rounds",
      title: "Rounds",
      numeric: false,
      value: (l) => l.row.rounds?.length,
      cell: (l) => <RoundsCell project={project} row={l.row} />,
    },
    {
      id: "attempts",
      title: "Attempts",
      numeric: true,
      value: (l) => l.item.attempts.length,
      cell: (l) => (l.sub ? "" : l.item.attempts.length),
    },
    {
      id: "spent",
      title: "Spent",
      numeric: true,
      value: (l) => (l.sub ? l.row.cost_usd : spent(l.item.attempts)) ?? undefined,
      cell: (l) => {
        const v = l.sub ? l.row.cost_usd : spent(l.item.attempts);
        return v === null ? "—" : usd(v);
      },
    },
    {
      id: "started",
      title: "Started",
      numeric: true,
      value: (l) => timeValue(l.row.started_at),
      cell: (l) => (l.row.started_at === null ? "—" : time(l.row.started_at)),
    },
    {
      id: "duration",
      title: "Duration",
      numeric: true,
      value: (l) => rowSeconds(l.row) ?? undefined,
      cell: (l) => {
        const s = rowSeconds(l.row);
        return s === null ? "—" : duration(s);
      },
    },
  ];
  const extra = project.columns.map((column, index): Spec => {
    const raw = (l: Line) => l.row.values?.[index];
    return {
      id: `column-${index.toString()}`,
      title: column.title,
      numeric: NUMERIC_FORMATS.has(column.format),
      value: (l) => {
        const v = raw(l);
        if (column.format === "time" && typeof v === "string")
          return timeValue(v);
        return typeof v === "number" || typeof v === "string" ? v : undefined;
      },
      cell: (l) => formatValue(raw(l), column.format),
    };
  });
  return [...fixed, ...extra];
}

/** ?sort=<column> ascending, ?sort=-<column> descending. */
function parseSort(param: string | null, ids: string[]): SortingState {
  if (param === null) return [];
  const desc = param.startsWith("-");
  const id = desc ? param.slice(1) : param;
  return ids.includes(id) ? [{ id, desc }] : [];
}

function StatusCell({ project, row }: { project: Project; row: AttemptRow }) {
  const label = <StatusLabel project={project} word={rowStatus(row)} />;
  const why = row.error ?? row.reason;
  if (why === null) return label;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <span tabIndex={0} className="inline-flex rounded-row">
          {label}
        </span>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="bottom"
          align="start"
          sideOffset={4}
          className="z-10 max-w-96 rounded-row border border-line bg-active px-2.5 py-1.5 text-xs break-words text-fg"
        >
          {why}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function RoundsCell({ project, row }: { project: Project; row: AttemptRow }) {
  if (!row.rounds || row.rounds.length === 0) return "—";
  return (
    <ol className="flex flex-wrap gap-1">
      {row.rounds.map((r) => {
        const outcome = statusOf(project, r.outcome);
        return (
          <li
            key={r.round}
            className="inline-flex h-5 items-center gap-1 rounded-row bg-neutral-bg px-1.5 text-xs"
          >
            {r.state !== null && (
              <span className="text-fg-2">{stateTitle(project, r.state)}</span>
            )}
            <span className={toneText[outcome.tone]}>{outcome.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

const button =
  "target inline-flex items-center gap-1.5 rounded-row border border-line px-2 text-xs font-medium hover:bg-hover";

export function HomeTable({
  project: given,
  read,
  reading,
  error,
  onRefresh,
}: {
  project: Project;
  read: Read;
  reading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const project = useMemo(() => withHomeStatuses(given), [given]);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const base = useId();
  const q = params.get("q") ?? "";
  const chosen = params.getAll("status");

  const items = useMemo(() => toItems(read.rows), [read.rows]);
  const chosenKey = chosen.join("\n");
  const filtered = useMemo(
    () => filterItems(project, items, q, chosenKey ? chosenKey.split("\n") : []),
    [project, items, q, chosenKey],
  );
  const columnSpecs = useMemo(() => specs(project), [project]);
  const columns = useMemo(
    () =>
      helper.columns(
        columnSpecs.map((s) =>
          helper.accessor((item) => s.value(itemLine(item)), {
            id: s.id,
            header: s.title,
            sortUndefined: "last",
            sortFn: compare,
          }),
        ),
      ),
    [columnSpecs],
  );
  const sorting = parseSort(
    params.get("sort"),
    columnSpecs.map((s) => s.id),
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
  const toggleChip = (key: string) => {
    update((p) => {
      const rest = p.getAll("status").filter((k) => k !== key);
      p.delete("status");
      for (const k of chosen.includes(key) ? rest : [...rest, key])
        p.append("status", k);
    });
  };

  const table = useTable({
    features,
    columns,
    data: filtered.shown,
    getRowId: (item) => item.id,
    getRowCanExpand: (row) => row.original.attempts.length > 1,
    state: { sorting },
    onSortingChange,
    enableMultiSort: false,
  });

  const open = (event: MouseEvent, href: string) => {
    if ((event.target as Element).closest("a, button, input, [tabindex]"))
      return;
    void navigate(href);
  };

  const rows = table.getRowModel().rows;
  const width = columnSpecs.length;
  const cellClass = (s: Spec) =>
    `px-3 py-1.5 align-top ${s.numeric ? "text-right tabular-nums" : ""}`;

  return (
    <Tooltip.Provider delayDuration={200}>
      <div className="flex flex-col gap-4 p-7">
        <div className="flex flex-wrap items-center gap-2">
          <label className="target inline-flex items-center gap-1.5 rounded-row border border-line px-2 focus-within:border-fg-3">
            <Search className="size-3.5 text-icon" aria-hidden />
            <input
              type="search"
              aria-label={`Search ${project.item.title} IDs`}
              placeholder={`Search ${project.item.title} IDs`}
              value={q}
              onChange={(e) => {
                const value = e.target.value;
                update((p) => {
                  if (value) p.set("q", value);
                  else p.delete("q");
                });
              }}
              className="h-6 w-56 bg-transparent text-xs outline-none placeholder:text-fg-3"
            />
          </label>
          <div
            role="group"
            aria-label="Filter by status"
            className="flex flex-wrap gap-1.5"
          >
            {filtered.chips.map(({ key, count }) => {
              const { label, tone } = statusOf(project, keyWord(key));
              const Glyph = toneGlyph[tone];
              return (
                <button
                  key={key}
                  type="button"
                  aria-pressed={chosen.includes(key)}
                  onClick={() => {
                    toggleChip(key);
                  }}
                  className={`${button} aria-pressed:border-fg-3 aria-pressed:bg-active`}
                >
                  <Glyph className={`size-3 ${toneText[tone]}`} aria-hidden />
                  {label}
                  <span className="text-fg-3 tabular-nums">{count}</span>
                </button>
              );
            })}
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs text-fg-3">
            {error !== null && <span className="text-bad">{error}</span>}
            <span>{reading ? "Reading…" : `Read ${time(read.at)}`}</span>
            <button type="button" onClick={onRefresh} className={button}>
              <RefreshCw className="size-3.5 text-icon" aria-hidden />
              Refresh
            </button>
          </div>
        </div>
        {filtered.hidden > 0 && (
          <p className="flex items-center gap-2 text-xs text-fg-3">
            {filtered.hidden} of {items.length}{" "}
            {items.length === 1 ? "item" : "items"} hidden by the search and
            filters.
            <button
              type="button"
              className={button}
              onClick={() => {
                update((p) => {
                  p.delete("q");
                  p.delete("status");
                });
              }}
            >
              Show all
            </button>
          </p>
        )}
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-fg-3">
              {table.getHeaderGroups()[0]?.headers.map((header, i) => {
                const spec = columnSpecs[i];
                const sorted = header.column.getIsSorted();
                const Arrow = sorted === "desc" ? ArrowDown : ArrowUp;
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={
                      sorted === "asc"
                        ? "ascending"
                        : sorted === "desc"
                          ? "descending"
                          : "none"
                    }
                    className={`px-3 py-1 font-medium ${spec?.numeric ? "text-right" : "text-left"}`}
                  >
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className={`target inline-flex items-center gap-1 rounded-row hover:text-fg-2 ${spec?.numeric ? "flex-row-reverse" : ""}`}
                    >
                      {spec?.title}
                      {sorted !== false && (
                        <Arrow className="size-3" aria-hidden />
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          {GROUPS.map(({ group, title }) => {
            const inGroup = rows.filter((r) => r.original.group === group);
            if (inGroup.length === 0) return null;
            const n = inGroup.length;
            const k = flagged(
              project,
              inGroup.map((r) => r.original),
            );
            return (
              <Group
                key={group}
                id={`${base}-${group}`}
                title={title}
                summary={`${n.toString()} ${n === 1 ? "item" : "items"}${k > 0 ? `, ${k.toString()} of ${n.toString()} bad or warn` : ""}`}
                width={width}
              >
                {inGroup.map((row, r) => {
                  const item = row.original;
                  const expanded = row.getIsExpanded();
                  const subIds = item.attempts.map(
                    (_, a) => `${base}-${group}-${r.toString()}-${a.toString()}`,
                  );
                  const lineCells = (line: Line, first: ReactNode) =>
                    columnSpecs.map((s, c) => (
                      <td key={s.id} className={cellClass(s)}>
                        {c === 0 ? first : s.cell(line)}
                      </td>
                    ));
                  const newest = attemptHref(project.id, item.newest.id);
                  return [
                    <tr
                      key={item.id}
                      onClick={(e) => {
                        open(e, newest);
                      }}
                      className="cursor-pointer border-b border-line-subtle hover:bg-hover"
                    >
                      {lineCells(
                        itemLine(item),
                        <span className="flex items-start gap-1">
                          {row.getCanExpand() ? (
                            <button
                              type="button"
                              aria-expanded={expanded}
                              aria-controls={subIds.join(" ")}
                              aria-label={`${expanded ? "Fold" : "Open"} the attempts of ${item.id}`}
                              onClick={() => {
                                row.toggleExpanded();
                              }}
                              className="target inline-grid place-items-center rounded-row text-icon hover:bg-active"
                            >
                              {expanded ? (
                                <ChevronDown className="size-3.5" aria-hidden />
                              ) : (
                                <ChevronRight
                                  className="size-3.5"
                                  aria-hidden
                                />
                              )}
                            </button>
                          ) : (
                            <span className="w-target flex-none" />
                          )}
                          <AttemptLink href={newest}>{item.id}</AttemptLink>
                        </span>,
                      )}
                    </tr>,
                    ...item.attempts.map((attempt, a) => {
                      const href = attemptHref(project.id, attempt.id);
                      return (
                        <tr
                          key={attempt.id}
                          id={subIds[a]}
                          hidden={!expanded}
                          onClick={(e) => {
                            open(e, href);
                          }}
                          className="cursor-pointer border-b border-line-subtle bg-panel text-fg-2 hover:bg-hover"
                        >
                          {lineCells(
                            { item, row: attempt, sub: true },
                            <span className="block pl-10">
                              <AttemptLink href={href}>{attempt.id}</AttemptLink>
                            </span>,
                          )}
                        </tr>
                      );
                    }),
                  ];
                })}
              </Group>
            );
          })}
        </table>
        {rows.length === 0 && (
          <p className="text-fg-3">
            {items.length === 0
              ? "The project has no attempts."
              : "No item matches."}
          </p>
        )}
      </div>
    </Tooltip.Provider>
  );
}

function AttemptLink({ href, children }: { href: string; children: string }) {
  return (
    <Link
      to={href}
      className="target inline-flex items-center font-mono text-xs break-all underline decoration-faint underline-offset-3"
    >
      {children}
    </Link>
  );
}

/** A group of rows under a heading row that folds it. */
function Group({
  id,
  title,
  summary,
  width,
  children,
}: {
  id: string;
  title: string;
  summary: string;
  width: number;
  children: ReactNode;
}) {
  const [folded, setFolded] = useState(false);
  return (
    <>
      <tbody>
        <tr className="border-b border-line">
          <th scope="rowgroup" colSpan={width} className="px-1 pt-3 pb-1 text-left">
            <button
              type="button"
              aria-expanded={!folded}
              aria-controls={id}
              onClick={() => {
                setFolded((f) => !f);
              }}
              className="target inline-flex items-center gap-1.5 rounded-row px-1.5 text-xs font-medium hover:bg-hover"
            >
              {folded ? (
                <ChevronRight className="size-3.5 text-icon" aria-hidden />
              ) : (
                <ChevronDown className="size-3.5 text-icon" aria-hidden />
              )}
              {title}
              <span className="font-normal text-fg-3">{summary}</span>
            </button>
          </th>
        </tr>
      </tbody>
      <tbody id={id} hidden={folded}>
        {children}
      </tbody>
    </>
  );
}
