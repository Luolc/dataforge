import {
  createColumnHelper,
  createSortedRowModel,
  type Row,
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
  outcomeOf,
  stateTitle,
  statusOf,
  toneGlyph,
  toneText,
} from "../../ui/status";
import { RunningIcon } from "../../ui/running";
import { StatusIcon, StatusLabel } from "../../ui/status-mark";
import { attemptHref } from "../attempt/href";
import {
  filterItems,
  flagged,
  GROUPS,
  isEmptyAttempt,
  keyWord,
  notEnded,
  rowSeconds,
  rowStatus,
  spent,
  toItems,
  type Item,
  withHomeStatuses,
} from "./items";

/** When the listing was read, and what it held. */
export interface Read {
  rows: AttemptRow[];
  /** ISO 8601. */
  at: string;
}

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
type TableRow = Row<typeof features, Item>;

// The values of one column are all numbers or all strings; sortUndefined
// places undefined before this is called.
const compare: SortFn<typeof features, Item> = (a, b, id) => {
  const x = a.getValue<Value>(id);
  const y = b.getValue<Value>(id);
  if (typeof x === "number" && typeof y === "number") return x - y;
  return String(x).localeCompare(String(y), undefined, { numeric: true });
};

/** An attempt's ID below its item's row: the part after the item's ID. */
function attemptName(item: Item, row: AttemptRow): string {
  const prefix = `${item.id}/`;
  return row.id.startsWith(prefix) ? row.id.slice(prefix.length) : row.id;
}

const itemLine = (item: Item): Line => ({
  item,
  row: item.newest,
  sub: false,
});

function timeValue(iso: string | null): Value {
  const t = iso === null ? NaN : Date.parse(iso);
  return Number.isNaN(t) ? undefined : t;
}

const NUMERIC_FORMATS = new Set(["number", "usd", "duration", "time"]);

/** A project column's value as its format says; "—" when there is none. */
function formatValue(value: unknown, format: Column["format"]): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (format === "usd") return usd(value);
    if (format === "duration") return duration(value);
    return String(value);
  }
  if (typeof value === "string") return format === "time" ? time(value) : value;
  return JSON.stringify(value);
}

const lineSpent = (l: Line) =>
  l.sub ? l.row.cost_usd : spent(l.item.attempts);

function specs(project: Project): Spec[] {
  const fixed: Spec[] = [
    {
      id: "icon",
      title: "",
      numeric: false,
      value: (l) => statusOf(project, rowStatus(l.row)).label,
      cell: (l) => <RowIcon project={project} row={l.row} />,
    },
    {
      id: "item",
      title: project.item.title,
      numeric: false,
      value: (l) => l.item.id,
      cell: (l) => (
        <span className={l.sub ? "block pl-4" : ""}>
          <AttemptLink href={attemptHref(project.id, l.row.id)}>
            {l.sub ? attemptName(l.item, l.row) : l.item.id}
          </AttemptLink>
        </span>
      ),
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
      cell: () => null, // drawn by the row, which holds its expansion
    },
    {
      id: "spent",
      title: "Spent",
      numeric: true,
      value: (l) => lineSpent(l) ?? undefined,
      cell: (l) => {
        const v = lineSpent(l);
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

/** A status label, with the record's reason, or why it cannot be read, on hover. */
function StatusCell({ project, row }: { project: Project; row: AttemptRow }) {
  const label = <StatusLabel project={project} word={rowStatus(row)} />;
  const why = row.error ?? row.reason;
  if (why === null) return label;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex -- the tooltip opens on focus, so a keyboard can reach it */}
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

/** The row's status as an icon: a spinner while it runs. */
function RowIcon({ project, row }: { project: Project; row: AttemptRow }) {
  const word = rowStatus(row);
  // The same box as the item's link beside it, so the icon stands on its
  // line.
  return (
    <span className="inline-flex target items-center">
      {word === "~running" ? (
        <RunningIcon />
      ) : (
        <StatusIcon project={project} word={word} />
      )}
    </span>
  );
}

/** One cell per round: its last visit's state and outcome. */
function RoundsCell({ project, row }: { project: Project; row: AttemptRow }) {
  if (!row.rounds || row.rounds.length === 0) return "—";
  return (
    <ol className="flex flex-wrap gap-1">
      {row.rounds.map((r) => {
        const outcome = outcomeOf(project, r.outcome, !notEnded(row));
        return (
          <li
            key={r.round}
            className="inline-flex h-5 items-center gap-1 rounded-row bg-neutral-bg px-1.5 text-xs"
          >
            {r.state !== null && (
              <span className="text-fg-2">{stateTitle(project, r.state)}</span>
            )}
            <span
              className={`inline-flex items-center gap-0.5 ${toneText[outcome.tone]}`}
            >
              <OutcomeGlyph tone={outcome.tone} />
              {outcome.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function OutcomeGlyph({ tone }: { tone: keyof typeof toneGlyph }) {
  const Glyph = toneGlyph[tone];
  return <Glyph className="size-icon-sm" aria-hidden />;
}

function AttemptLink({ href, children }: { href: string; children: string }) {
  return (
    <Link
      to={href}
      className="inline-flex target items-center font-mono text-xs break-words underline decoration-faint underline-offset-3"
    >
      {children}
    </Link>
  );
}

const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover";

/** A project's items in their groups, with search, status chips and refresh. */
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
  const baseId = useId();
  const q = params.get("q") ?? "";
  const chosen = params.get("status");

  const showOlder = params.get("older") === "1";
  const older = useMemo(
    () => read.rows.filter(isEmptyAttempt).length,
    [read.rows],
  );
  const items = useMemo(
    () =>
      toItems(
        showOlder ? read.rows : read.rows.filter((r) => !isEmptyAttempt(r)),
      ),
    [read.rows, showOlder],
  );
  const filtered = useMemo(
    () => filterItems(project, items, q, chosen),
    [project, items, q, chosen],
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
  const sortParam = params.get("sort");
  const sorting = useMemo(
    () =>
      parseSort(
        sortParam,
        columnSpecs.map((s) => s.id),
      ),
    [sortParam, columnSpecs],
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
  const choose = (key: string | null) => {
    update((p) => {
      if (key === null) p.delete("status");
      else p.set("status", key);
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
  const rows = table.getRowModel().rows;

  // A click on a row opens its attempt, but not one on a control in it.
  const open = (event: MouseEvent, href: string) => {
    if (
      event.target instanceof Element &&
      event.target.closest("a, button, [tabindex]")
    )
      return;
    void navigate(href);
  };

  const cellClass = (s: Spec) =>
    s.id === "icon"
      ? "w-8 py-1.5 pr-0 pl-3 align-top"
      : `px-3 py-1.5 align-top ${s.numeric ? "text-right whitespace-nowrap tabular-nums" : ""} ${s.id === "status" || s.id === "item" ? "whitespace-nowrap" : ""}`;

  const lineRow = (
    line: Line,
    attempts: ReactNode,
    props: { id?: string; hidden?: boolean; className: string },
  ) => {
    const href = attemptHref(project.id, line.row.id);
    return (
      <tr
        key={line.sub ? line.row.id : `item:${line.item.id}`}
        {...props}
        onClick={(e) => {
          open(e, href);
        }}
      >
        {columnSpecs.map((s) => (
          <td key={s.id} className={cellClass(s)}>
            {s.id === "attempts" ? attempts : s.cell(line)}
          </td>
        ))}
      </tr>
    );
  };

  const itemRows = (row: TableRow, prefix: string) => {
    const item = row.original;
    const expanded = row.getIsExpanded();
    const subId = (a: number) => `${prefix}-${a.toString()}`;
    const subIds = item.attempts.map((_, a) => subId(a));
    const count = item.attempts.length;
    const attempts = row.getCanExpand() ? (
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={subIds.join(" ")}
        aria-label={`${count.toString()} attempts of ${item.id}`}
        onClick={() => {
          row.toggleExpanded();
        }}
        className="inline-flex target items-center gap-0.5 rounded-row px-1 hover:bg-active"
      >
        {expanded ? (
          <ChevronDown className="size-3.5 text-icon" aria-hidden />
        ) : (
          <ChevronRight className="size-3.5 text-icon" aria-hidden />
        )}
        {count}
      </button>
    ) : (
      count
    );
    return [
      lineRow(itemLine(item), attempts, {
        className: "cursor-pointer border-b border-line-subtle hover:bg-hover",
      }),
      ...(row.getCanExpand()
        ? item.attempts.map((attempt, a) =>
            lineRow({ item, row: attempt, sub: true }, null, {
              id: subId(a),
              hidden: !expanded,
              className:
                "cursor-pointer border-b border-line-subtle bg-app hover:bg-hover",
            }),
          )
        : []),
    ];
  };

  return (
    <Tooltip.Provider delayDuration={200}>
      <div className="flex flex-col gap-3 px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Filter by status"
            className="flex flex-wrap gap-1.5"
          >
            <Chip
              pressed={chosen === null}
              onClick={() => {
                choose(null);
              }}
              label="All"
              count={filtered.searched}
            />
            {filtered.chips.map(({ key, count }) => {
              const { label, tone } = statusOf(project, keyWord(key));
              const Glyph = toneGlyph[tone];
              return (
                <Chip
                  key={key}
                  pressed={chosen === key}
                  onClick={() => {
                    choose(chosen === key ? null : key);
                  }}
                  icon={
                    <Glyph
                      className={`size-icon-sm ${toneText[tone]}`}
                      aria-hidden
                    />
                  }
                  label={label}
                  count={count}
                />
              );
            })}
          </div>
          <label className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 focus-within:border-fg-3">
            <Search className="size-3.5 text-icon" aria-hidden />
            <input
              type="search"
              aria-label={`Search ${project.item.title} IDs`}
              placeholder={`Search ${project.item.title} IDs`}
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
            <span aria-live="polite">
              {reading ? "Reading…" : `Read ${time(read.at)}`}
            </span>
            <button type="button" onClick={onRefresh} className={control}>
              <RefreshCw className="size-3.5 text-icon" aria-hidden />
              Refresh
            </button>
          </div>
        </div>
        {filtered.hidden > 0 && (
          <p className="flex items-center gap-2 text-xs text-fg-3">
            {filtered.hidden} of {items.length} hidden by{" "}
            {q.trim() !== "" && chosen !== null
              ? "the search and the status filter"
              : q.trim() !== ""
                ? "the search"
                : "the status filter"}
            .
            <button
              type="button"
              className={control}
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
            <tr className="border-b border-line bg-head text-xs text-fg-2">
              {table.getHeaderGroups()[0]?.headers.map((header, i) => {
                const spec = columnSpecs[i];
                const sorted = header.column.getIsSorted();
                const Arrow = sorted === "desc" ? ArrowDown : ArrowUp;
                if (spec?.id === "icon")
                  return (
                    <th key={header.id} scope="col" className="w-8">
                      <span className="sr-only">Status icon</span>
                    </th>
                  );
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
                    className={`px-3 py-1 font-medium ${spec?.numeric === true ? "text-right" : "text-left"}`}
                  >
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className={`inline-flex target items-center gap-1 rounded-row hover:text-fg-2 ${spec?.numeric === true ? "flex-row-reverse" : ""}`}
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
            const summary =
              k > 0
                ? `${k.toString()} of ${n.toString()} bad or warn`
                : n.toString();
            return (
              <Group
                key={group}
                id={`${baseId}-${group}`}
                title={title}
                summary={summary}
                width={columnSpecs.length}
              >
                {inGroup.map((row, r) =>
                  itemRows(row, `${baseId}-${group}-${r.toString()}`),
                )}
              </Group>
            );
          })}
        </table>
        {rows.length === 0 && (
          <p className="py-6 text-center text-fg-3">
            {read.rows.length === 0
              ? "The project has no attempts."
              : items.length === 0
                ? "Every attempt of the project is an older record."
                : "Nothing matches the search and the status filter."}
          </p>
        )}
        {older > 0 && (
          <p className="flex items-center gap-2 text-xs text-fg-3">
            {`${older.toString()} older ${older === 1 ? "record" : "records"} ${showOlder ? "shown" : "hidden"}`}
            <span aria-hidden>·</span>
            <button
              type="button"
              className={control}
              aria-pressed={showOlder}
              onClick={() => {
                update((p) => {
                  if (showOlder) p.delete("older");
                  else p.set("older", "1");
                });
              }}
            >
              {showOlder ? "Hide" : "Show"}
            </button>
          </p>
        )}
      </div>
    </Tooltip.Provider>
  );
}

function Chip({
  pressed,
  onClick,
  icon,
  label,
  count,
}: {
  pressed: boolean;
  onClick: () => void;
  icon?: ReactNode;
  label: string;
  count: number;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`${control} group bg-chip aria-pressed:border-chip-on aria-pressed:bg-chip-on aria-pressed:text-chip-on-fg`}
    >
      {icon}
      {label}
      <span className="font-normal text-fg-2 tabular-nums group-aria-pressed:text-chip-on-count">
        {count}
      </span>
    </button>
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
        <tr>
          <th
            scope="rowgroup"
            colSpan={width}
            className="border-y border-line-subtle bg-group px-1 py-0.5 text-left"
          >
            <button
              type="button"
              aria-expanded={!folded}
              aria-controls={id}
              onClick={() => {
                setFolded((f) => !f);
              }}
              className="inline-flex target items-center gap-1.5 rounded-row px-1.5 text-xs font-medium hover:bg-hover"
            >
              {folded ? (
                <ChevronRight className="size-3.5 text-icon" aria-hidden />
              ) : (
                <ChevronDown className="size-3.5 text-icon" aria-hidden />
              )}
              {title}
              <span className="font-normal text-fg-2">{summary}</span>
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
