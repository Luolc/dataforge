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
  ChevronDown,
  ChevronRight,
  RefreshCw,
  ScrollText,
  Search,
} from "lucide-react";
import { Tooltip } from "radix-ui";
import {
  type ReactNode,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useLocation, useNavigate } from "react-router";

import type { AttemptRow, Project } from "../../api/types";
import { duration, time, usd } from "../../ui/format";
import {
  liveStatus,
  outcomeOf,
  stateTitle,
  statusOf,
  toneGlyph,
  toneText,
} from "../../ui/status";
import { Chip, control } from "../../ui/chip";
import { ColumnPicker } from "../../ui/column-picker";
import { type FilterField, FilterButton, FilterRules } from "../../ui/filter";
import { type Rule, withRule } from "../../ui/rule";
import { Hint } from "../../ui/hint";
import { Group } from "../../ui/group";
import { Sideways } from "../../ui/sideways";
import { Elapsed, RunningIcon } from "../../ui/running";
import { StatusIcon, StatusLabel } from "../../ui/status-mark";
import { attemptHref } from "../attempt/href";
import { stoppedWhy } from "../queue/status";
import {
  type Cell,
  columnSpec,
  compareValues,
  joined,
  parseSort,
  ROW_CLASS,
  type Spec as ColumnSpec,
  timeValue,
  useOpenRow,
  type Value,
} from "./columns";
import { SortHeading } from "./ColumnControls";
import {
  counted,
  filterItems,
  flagged,
  GROUPS,
  homeFields,
  isEmptyAttempt,
  keyWord,
  NO_VALUE,
  notEnded,
  rowSeconds,
  roundRuns,
  rowStatus,
  spent,
  STATUS_FIELD,
  toItems,
  type Item,
  withHomeStatuses,
} from "./items";
import {
  columnKey,
  filterFields,
  loadHidden,
  loadRules,
  readQuery,
  saveHidden,
  saveRules,
  writeQuery,
} from "./view";

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

/** A column, with its key in the view a reader sets (view.ts). */
interface Spec extends ColumnSpec<Line> {
  key: string;
}

/** The columns a reader cannot hide: the item's ID, and its status icon beside it. */
const LOCKED = new Set(["icon", "item"]);

const features = tableFeatures({
  rowSortingFeature,
  rowExpandingFeature,
  sortedRowModel: createSortedRowModel(),
});
const helper = createColumnHelper<typeof features, Item>();
type TableRow = Row<typeof features, Item>;

// The values of one column are all numbers or all strings; sortUndefined
// places undefined before this is called.
const compare: SortFn<typeof features, Item> = (a, b, id) =>
  compareValues(a.getValue<Value>(id), b.getValue<Value>(id));

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

/** A row's end, null while it has not ended. */
const lineEnded = (l: Line) => (notEnded(l.row) ? null : l.row.ended_at);

function specs(project: Project): Spec[] {
  const fixed: ColumnSpec<Line>[] = [
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
      // An item's row is its newest attempt, as its other cells are; what all
      // of its attempts spent, and when a store's row last read its cost, are
      // on hover.
      value: (l) => l.row.cost_usd ?? undefined,
      cell: (l) => {
        const shown = l.row.cost_usd === null ? "—" : usd(l.row.cost_usd);
        const total = spent(l.item.attempts);
        const read = l.row.progress_at ?? null;
        const hints = [
          ...(l.row.cost_from === "runs"
            ? ["From its runs; its record has no budget."]
            : []),
          ...(l.row.cost_usd !== null && read !== null
            ? [`Read ${time(read)}.`]
            : []),
          ...(!l.sub && l.item.attempts.length >= 2 && total !== null
            ? [
                `${usd(total)} spent over its ${counted(l.item.attempts.length, "attempt")}.`,
              ]
            : []),
        ];
        if (hints.length === 0) return shown;
        return (
          <Hint hint={hints.join(" ")}>
            {/* Dotted, as a hint's sign. */}
            <span className="underline decoration-faint decoration-dotted underline-offset-3">
              {shown}
            </span>
          </Hint>
        );
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
      id: "ended",
      title: "Ended",
      numeric: true,
      value: (l) => timeValue(lineEnded(l)),
      cell: (l) => {
        const ended = lineEnded(l);
        return ended === null ? "—" : time(ended);
      },
    },
    {
      id: "duration",
      title: "Duration",
      numeric: true,
      value: (l) => rowSeconds(l.row) ?? undefined,
      cell: (l) => {
        // While it runs, it goes on from its start, and sorts last, as a row
        // with no duration does.
        if (l.row.live === "running" && l.row.started_at !== null)
          return <Elapsed since={l.row.started_at} />;
        const s = rowSeconds(l.row);
        return s === null ? "—" : duration(s);
      },
    },
  ];
  const extra = project.columns.map((column, index) => ({
    ...columnSpec(column, index, (l: Line): Cell => {
      const read = l.row.reads?.[index] ?? null;
      return {
        value: l.row.values?.[index],
        read,
        queued: l.row.queued?.[index] ?? null,
      };
    }),
    key: columnKey(column.title),
  }));
  return [...fixed.map((s) => ({ ...s, key: s.id })), ...extra];
}

// The status icon and the item stay at the left as the table scrolls
// sideways, so a row is known at its right; the icon's column is
// w-row-icon wide, the item's edged on its right.
const STUCK: Partial<Record<string, string>> = {
  icon: "sticky left-0 z-1",
  item: "sticky left-row-icon z-1 shadow-stuck",
};

/** A heading's class when its column stays at the left, on the head's surface. */
function stuckHead(id: string): string {
  const at = STUCK[id];
  return at === undefined ? "" : `${at} bg-head`;
}

/**
 * A status label, with the record's reason, what a reconcile has yet to do
 * with its row, or why it cannot be read, on hover.
 */
function StatusCell({ project, row }: { project: Project; row: AttemptRow }) {
  const label = <StatusLabel project={project} word={rowStatus(row)} />;
  const why =
    row.error ??
    row.note ??
    (row.live === "may have stopped" && row.stopped != null
      ? stoppedWhy(row.stopped)
      : row.reason);
  if (why === null) return label;
  return <Hint hint={why}>{label}</Hint>;
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

/**
 * One cell per round, its number and its last visit's state and outcome,
 * rounds in a row that ended alike in one cell, with how many they are; the
 * round that runs now as Running.
 */
function RoundsCell({ project, row }: { project: Project; row: AttemptRow }) {
  if (!row.rounds || row.rounds.length === 0) return "—";
  const running = row.live === "running" && row.rounds.at(-1)?.outcome === null;
  const runs = roundRuns(row.rounds, running);
  return (
    <ol className="flex flex-wrap gap-1">
      {runs.map((r, i) => {
        const outcome = outcomeOf(project, r.outcome, !notEnded(row));
        const now = running && i === runs.length - 1;
        return (
          <li
            key={r.first}
            className="inline-flex h-5 items-center gap-1 rounded-row bg-neutral-bg px-1.5 text-xs whitespace-nowrap"
          >
            <span className="text-fg-3 tabular-nums">
              {r.count > 1
                ? `R${r.first.toString()}–R${r.last.toString()}`
                : `R${r.first.toString()}`}
            </span>
            {r.state !== null && (
              <span className="text-fg-2">{stateTitle(project, r.state)}</span>
            )}
            {now ? (
              <span
                className={`inline-flex items-center gap-0.5 ${toneText[liveStatus.running.tone]}`}
              >
                {/* The word beside it says it. */}
                <span aria-hidden className="inline-flex">
                  <RunningIcon />
                </span>
                {liveStatus.running.label}
              </span>
            ) : (
              <span
                className={`inline-flex items-center gap-0.5 ${toneText[outcome.tone]}`}
              >
                <OutcomeGlyph tone={outcome.tone} />
                {outcome.label}
              </span>
            )}
            {r.count > 1 && (
              <span className="text-fg-2 tabular-nums">
                ×{r.count.toString()}
              </span>
            )}
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
  const location = useLocation();
  const navigate = useNavigate();
  const open = useOpenRow();
  const baseId = useId();
  const filterRef = useRef<HTMLButtonElement>(null);
  const query = useMemo(
    () => readQuery(location.search, project.columns),
    [location.search, project.columns],
  );
  const params = query.others;
  const q = params.get("q") ?? "";
  // The URL's rules, else those kept for the project (design-viz.md
  // section 5, "Columns and filter").
  const rules = useMemo(
    () =>
      query.rules.length > 0
        ? query.rules
        : loadRules(project.id, filterFields(project.columns)),
    [query, project.id, project.columns],
  );
  const search = writeQuery(params, rules);
  // A link's rules become the project's; the URL gives the rules, those of
  // a link of the older kind rewritten, and none it drops.
  useEffect(() => {
    if (query.rules.length > 0) saveRules(project.id, query.rules);
    if (search !== location.search)
      void navigate({ search }, { replace: true });
  }, [query, search, location.search, project.id, navigate]);

  const showOlder = params.get("older") === "1";
  const older = useMemo(() => {
    const rows = read.rows.filter(isEmptyAttempt);
    return {
      attempts: rows.length,
      items: new Set(rows.map((r) => r.item)).size,
    };
  }, [read.rows]);
  const itemTitle = project.item.title;
  const items = useMemo(
    () =>
      toItems(
        showOlder ? read.rows : read.rows.filter((r) => !isEmptyAttempt(r)),
      ),
    [read.rows, showOlder],
  );
  const fields = useMemo(() => homeFields(project), [project]);
  const filtered = useMemo(
    () => filterItems(project, items, q, rules, fields),
    [project, items, q, rules, fields],
  );
  // Each field as the Filter panel shows it, its title and its values'.
  const filterPanel = useMemo(
    (): FilterField[] =>
      fields.map((field, i) => {
        const column = project.columns.find(
          (c) => columnKey(c.title) === field.key,
        );
        return {
          key: field.key,
          title: column?.title ?? "Status",
          values: (filtered.fields[i]?.values ?? []).map(({ key, count }) => {
            if (column !== undefined)
              return {
                key,
                count,
                label:
                  key === NO_VALUE ? "No value" : (column.labels?.[key] ?? key),
              };
            const { label, tone } = statusOf(project, keyWord(key));
            const Glyph = toneGlyph[tone];
            return {
              key,
              count,
              label,
              icon: (
                <Glyph
                  className={`size-icon-sm flex-none ${toneText[tone]}`}
                  aria-hidden
                />
              ),
            };
          }),
        };
      }),
    [fields, filtered.fields, project],
  );
  const statusCounts = filtered.fields[0];
  const statusRule = rules.find((r) => r.field === STATUS_FIELD);
  // A status chip is pressed while the Status rule is any of its status alone.
  const chosen =
    statusRule?.op === "any" && statusRule.values.length === 1
      ? (statusRule.values[0] ?? null)
      : null;
  const allSpecs = useMemo(() => specs(project), [project]);
  const pickable = useMemo(
    () =>
      allSpecs
        .filter((s) => s.id !== "icon")
        .map((s) => ({ key: s.key, title: s.title, locked: LOCKED.has(s.id) })),
    [allSpecs],
  );
  const [hidden, setHidden] = useState(() =>
    loadHidden(
      project.id,
      pickable.filter((c) => !c.locked).map((c) => c.key),
    ),
  );
  const columnSpecs = useMemo(
    () => allSpecs.filter((s) => !hidden.includes(s.key)),
    [allSpecs, hidden],
  );
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

  const update = (
    change: (next: URLSearchParams) => void,
    nextRules: readonly Rule[] = rules,
  ) => {
    const next = new URLSearchParams(params);
    change(next);
    void navigate({ search: writeQuery(next, nextRules) }, { replace: true });
  };
  // Each change of the rules is kept and written to the URL.
  const setRules = (next: Rule[]) => {
    saveRules(project.id, next);
    update(() => undefined, next);
  };
  const onSortingChange = (updater: Updater<SortingState>) => {
    const next = typeof updater === "function" ? updater(sorting) : updater;
    const first = next[0];
    update((p) => {
      if (first) p.set("sort", `${first.desc ? "-" : ""}${first.id}`);
      else p.delete("sort");
    });
  };
  const onHide = (next: string[]) => {
    setHidden(next);
    saveHidden(project.id, next);
    // A column hidden takes its sort with it, and the rows go back to their
    // order: a sort by a hidden column, which no heading would show, is none.
    const by = allSpecs.find((s) => s.id === sorting[0]?.id);
    if (by !== undefined && next.includes(by.key))
      update((p) => {
        p.delete("sort");
      });
  };
  const choose = (key: string | null) => {
    setRules(
      withRule(
        rules,
        STATUS_FIELD,
        key === null ? null : { field: STATUS_FIELD, op: "any", values: [key] },
      ),
    );
  };
  const showAll = () => {
    saveRules(project.id, []);
    update((p) => {
      p.delete("q");
    }, []);
  };
  // What hides items, as the line under the filters names it.
  const filters = [
    ...(q.trim() !== "" ? ["the search"] : []),
    ...rules.map(
      (r) =>
        `the ${filterPanel.find((f) => f.key === r.field)?.title ?? r.field} filter`,
    ),
  ];

  const table = useTable({
    features,
    columns,
    data: filtered.shown,
    getRowId: (item) => item.id,
    getRowCanExpand: (row) => row.original.attempts.length > 1,
    state: { sorting },
    // A read of the listing gives new data; what a person opened stays open.
    autoResetExpanded: false,
    onSortingChange,
    enableMultiSort: false,
  });
  const rows = table.getRowModel().rows;

  const cellClass = (s: Spec, sub: boolean) => {
    // On the row's own surface, which the cells it stands over pass under.
    const at = STUCK[s.id];
    const stuck =
      at === undefined
        ? ""
        : `${at} ${sub ? "bg-app" : "bg-panel"} group-hover/row:bg-hover`;
    return s.id === "icon"
      ? `w-row-icon py-1.5 pr-0 pl-3 align-top ${stuck}`
      : `px-3 py-1.5 align-top ${s.numeric ? "text-right whitespace-nowrap tabular-nums" : ""} ${s.id === "status" || s.id === "item" ? "whitespace-nowrap" : ""} ${stuck}`;
  };

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
          <td key={s.id} className={cellClass(s, line.sub)}>
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
        className: ROW_CLASS,
      }),
      ...(row.getCanExpand()
        ? item.attempts.map((attempt, a) =>
            lineRow({ item, row: attempt, sub: true }, null, {
              id: subId(a),
              hidden: !expanded,
              className: `${ROW_CLASS} bg-app`,
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
              pressed={statusRule === undefined}
              onClick={() => {
                choose(null);
              }}
              label="All"
              count={statusCounts?.all ?? 0}
              hint={counted(statusCounts?.all ?? 0, itemTitle)}
            />
            {(statusCounts?.values ?? []).map(({ key, count }) => {
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
                  hint={counted(count, itemTitle)}
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
            {project.definition != null && (
              <Link
                to={`/${encodeURIComponent(project.id)}/definition`}
                className={control}
              >
                <ScrollText className="size-3.5 text-icon" aria-hidden />
                Definition
              </Link>
            )}
            <button type="button" onClick={onRefresh} className={control}>
              <RefreshCw className="size-3.5 text-icon" aria-hidden />
              Refresh
            </button>
            <FilterButton
              ref={filterRef}
              fields={filterPanel}
              rules={rules}
              onChange={setRules}
            />
            <ColumnPicker
              columns={pickable}
              hidden={hidden}
              onChange={onHide}
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 empty:hidden">
          <FilterRules
            fields={filterPanel}
            rules={rules}
            onChange={setRules}
            fallback={filterRef}
          />
          {filtered.hidden > 0 && (
            <p className="flex items-center gap-2 text-xs text-fg-3">
              {filtered.hidden} of {counted(items.length, itemTitle)} hidden by{" "}
              {joined(filters)}.
              <button type="button" className={control} onClick={showAll}>
                Show all
              </button>
            </p>
          )}
        </div>
        {/* The table scrolls sideways on its own, under the filters. */}
        <Sideways label="the table">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line bg-head text-xs text-fg-2">
                {table.getHeaderGroups()[0]?.headers.map((header, i) => {
                  const spec = columnSpecs[i];
                  const sorted = header.column.getIsSorted();
                  if (spec?.id === "icon")
                    return (
                      <th
                        key={header.id}
                        scope="col"
                        className={`w-row-icon ${stuckHead("icon")}`}
                      >
                        <span className="sr-only">Status icon</span>
                      </th>
                    );
                  return (
                    <SortHeading
                      key={header.id}
                      spec={spec}
                      sorted={sorted}
                      onSort={header.column.getToggleSortingHandler()}
                      className={spec === undefined ? "" : stuckHead(spec.id)}
                    />
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
        </Sideways>
        {rows.length === 0 && (
          <p className="flex items-center justify-center gap-2 py-6 text-fg-3">
            {read.rows.length === 0 ? (
              "The project has no attempts."
            ) : items.length === 0 ? (
              "Every attempt of the project is an older attempt."
            ) : (
              <>
                {`No ${itemTitle} matches ${joined(filters)}.`}
                <button type="button" className={control} onClick={showAll}>
                  Show all
                </button>
              </>
            )}
          </p>
        )}
        {older.attempts > 0 && (
          <p className="flex items-center gap-2 text-xs text-fg-3">
            {`${counted(older.attempts, "older attempt")} ${showOlder ? "shown" : "hidden"} (${counted(older.items, itemTitle)})`}
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
