// The only place a deliverable's kind picks what shows it: the view in its
// tab, whether several of the kind are listed in a column or one is chosen
// from a menu, the
// tab's count and one line on its card in the Summary.
import { CircleAlert } from "lucide-react";
import { type ComponentType } from "react";
import { Link, useSearchParams } from "react-router";

import { useFileCount } from "../api/client";
import type {
  AttemptRecord,
  Deliverable,
  LiveInfo,
  Project,
} from "../api/types";
import { TabBadge } from "../ui/count";
import { counted } from "../ui/format";
import { Note } from "../ui/card";
import { ChoiceMenu } from "../ui/menu";
import { SideColumn, sideEntry } from "../ui/side-column";
import { NOT_RUN } from "../ui/status";
import { DeliverableCard } from "./DeliverableCard";
import { useNewest } from "./newest";
import FilesView from "./files";
import {
  declared,
  type DeliverableProps,
  kindTitle,
  leadersFirst,
  leading,
  reachedFirst,
} from "./kind";
import ReportView from "./report";
import TrialsView from "./trials";
import { COUNTED } from "./trials/status";

interface TabProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  items: Deliverable[];
  live?: LiveInfo | null;
}

interface ItemProps {
  project: string;
  attempt: string;
  item: Deliverable;
  /** The word after a count, as the project file names it for the item. */
  word?: string | null | undefined;
}

interface Kind {
  View: ComponentType<DeliverableProps>;
  /**
   * List every deliverable of the kind, each its own entry of a column
   * beside the one shown, not in a menu, and count them all.
   */
  listed: boolean;
  /**
   * The tab's count, of the deliverable given for a kind that shows one at
   * a time, else of every deliverable of the kind; said with `of` before it.
   */
  Count: ComponentType<ItemProps & { items: Deliverable[]; of: string }>;
  /** One line on a deliverable's card, when its path was reached. */
  Line: ComponentType<ItemProps>;
}

/** How many files are under a deliverable's path; nothing while loading. */
function FileCount({ project, attempt, item }: ItemProps) {
  const count = useFileCount(project, attempt, item.path);
  if (count.state === "loading") return null;
  if (count.state === "error") return <CountFailed message={count.message} />;
  return <>{`${count.data.files}${count.data.complete ? "" : "+"}`}</>;
}

/** The same count on a tab, with its unit in full. */
function FileBadge({ of, ...props }: ItemProps & { of: string }) {
  const count = useFileCount(props.project, props.attempt, props.item.path);
  if (count.state === "loading")
    return <TabBadge label={`${of}Counting the files`}>{null}</TabBadge>;
  if (count.state === "error")
    return (
      <TabBadge label={`${of}Could not count the files: ${count.message}`}>
        <CountFailed message={count.message} />
      </TabBadge>
    );
  const { files, complete } = count.data;
  return (
    <TabBadge
      label={
        of +
        (complete
          ? counted(files, "file")
          : `At least ${counted(files, "file")}`)
      }
    >
      {`${files.toString()}${complete ? "" : "+"}`}
    </TabBadge>
  );
}

function CountFailed({ message }: { message: string }) {
  return (
    <CircleAlert
      role="img"
      aria-label={`Could not count the files: ${message}`}
      className="inline size-3 text-bad"
    />
  );
}

/**
 * How many of a trials deliverable's k trials it counts, as `word` says,
 * short and in full.
 */
function solved(
  item: Deliverable,
  word: string,
): { short: string; full: string } {
  if (item.path === null) return { short: NOT_RUN, full: NOT_RUN };
  const value = item.value?.value;
  const k = (item.k ?? 0).toString();
  if (typeof value === "number" || typeof value === "string")
    return {
      short: `${String(value)} / ${k}`,
      full: `${String(value)} of ${k} ${word}`,
    };
  return {
    short: `— / ${k}`,
    full: `${counted(item.k ?? 0, "trial")}, ${word} not known`,
  };
}

/** A count of the deliverables of a kind the attempt reached; Not run with none. */
function reachedBadge(items: Deliverable[], unit: string) {
  const reached = items.filter((item) => item.path !== null).length;
  return reached === 0 ? (
    <TabBadge label={NOT_RUN} />
  ) : (
    <TabBadge label={counted(reached, unit)}>{reached}</TabBadge>
  );
}

const kinds: Partial<Record<string, Kind>> = {
  files: {
    View: FilesView,
    listed: false,
    Count: ({ of, ...props }) =>
      props.item.path === null ? (
        <TabBadge label={of + NOT_RUN}>{NOT_RUN}</TabBadge>
      ) : (
        <FileBadge of={of} {...props} />
      ),
    Line: (props) => (
      <>
        <FileCount {...props} /> files
      </>
    ),
  },
  trials: {
    View: TrialsView,
    listed: false,
    Count: ({ item, of, word }) => {
      const { short, full } = solved(item, word ?? COUNTED);
      return <TabBadge label={of + full}>{short}</TabBadge>;
    },
    Line: ({ item, word }) =>
      `${solved(item, word ?? COUNTED).short} ${word ?? COUNTED}`,
  },
  report: {
    View: ReportView,
    listed: true,
    Count: ({ items }) => reachedBadge(items, "report"),
    Line: ({ item }) => (
      <code className="font-mono text-xs">{item.path?.split("/").at(-1)}</code>
    ),
  },
};

const unknown: Kind = {
  View: ({ deliverable }) => <DeliverableCard item={deliverable} />,
  listed: true,
  Count: ({ items }) => reachedBadge(items, "deliverable"),
  Line: ({ item }) => <code className="font-mono text-xs">{item.kind}</code>,
};

/** What shows deliverables of a kind; an unknown kind shows as cards. */
function kindOf(kind: string): Kind {
  return kinds[kind] ?? unknown;
}

/**
 * The count on a kind's tab, of every deliverable of the kind for one that
 * stacks them, else of the one the tab opens on, named before the count when
 * the tab's title is not its own.
 */
export function KindCount({
  kind,
  title,
  ...props
}: ItemProps & { items: Deliverable[]; kind: string; title: string }) {
  const { Count, listed } = kindOf(kind);
  const of =
    listed || title === props.item.title ? "" : `${props.item.title}: `;
  return <Count of={of} {...props} />;
}

/** A deliverable's line on its card in the Summary. */
export function KindLine({ item, ...props }: ItemProps) {
  const { Line } = kindOf(item.kind);
  return <Line item={item} {...props} />;
}

/**
 * A kind's tab: one deliverable, the one the page's `deliverable` names, else
 * the leading one; chosen, for a kind that lists them, from a column of all
 * of them, those reached first, else from a menu.
 */
export function KindTab({ kind, ...props }: TabProps & { kind: string }) {
  const { View, listed, Count } = kindOf(kind);
  const [query, setQuery] = useSearchParams();
  const asked = props.items.find((d) => d.name === query.get("deliverable"));
  const shown = asked ?? leading(props.project, props.record, props.items);
  const setChosen = (name: string) => {
    setQuery((before) => withDeliverable(before, name));
  };
  const newest = useNewest(shown?.name ?? "");
  // Until /newest has said which file it is, the record's may be the older.
  const view =
    shown &&
    (newest.state === "loading" ? (
      <Note>Loading…</Note>
    ) : (
      <>
        {newest.state === "error" && (
          <p className="text-xs text-fg-3">
            {`Could not read which directory gives this deliverable: ${newest.message}. Shown from the record.`}
          </p>
        )}
        <View
          key={shown.name}
          project={props.project}
          attempt={props.attempt}
          record={props.record}
          deliverable={shown}
          live={props.live ?? null}
        />
      </>
    ));
  if (listed && props.items.length > 1)
    return (
      <div className="px-7 pt-5 pb-12">
        <SideColumn
          label={kindTitle(props.project, props.record, kind, props.items)}
          entries={reachedFirst(props.project, props.record, props.items).map(
            (item) => (
              <Link
                key={item.name}
                to={{
                  search: `?${withDeliverable(query, item.name).toString()}`,
                }}
                aria-current={item === shown ? "page" : undefined}
                className={`${sideEntry} justify-between`}
              >
                <span className="wrap-anywhere">{item.title}</span>
                {item.path === null && (
                  <span className="flex-none text-xs font-normal text-fg-3">
                    {NOT_RUN}
                  </span>
                )}
              </Link>
            ),
          )}
        >
          <div className="flex min-w-0 flex-col gap-3">{view}</div>
        </SideColumn>
      </div>
    );
  return (
    <div className="flex flex-col gap-3 px-7 pt-5 pb-12">
      {props.items.length > 1 && (
        <ChoiceMenu
          items={leadersFirst(props.project, props.record, props.items).map(
            (item) => ({
              ...choice(item),
              aside: (
                <Count
                  of=""
                  project={props.project.id}
                  attempt={props.attempt}
                  item={item}
                  items={[item]}
                  word={declared(props.project, item.name)?.count}
                />
              ),
            }),
          )}
          chosen={shown && choice(shown)}
          onChoose={setChosen}
        />
      )}
      {view}
    </div>
  );
}

/** The page's query with the deliverable named, on the attempt page as on the share site. */
function withDeliverable(query: URLSearchParams, name: string) {
  const next = new URLSearchParams(query);
  next.set("deliverable", name);
  // What else the page shows belongs to the deliverable it leaves.
  next.delete("trial");
  next.delete("file");
  return next;
}

const choice = (item: Deliverable) => ({ key: item.name, title: item.title });
