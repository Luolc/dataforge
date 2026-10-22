// The only place a deliverable's kind picks what shows it: the view in its
// tab, whether several of the kind stack or one is chosen from a menu, the
// tab's count and one line on its card in the Summary.
import { CircleAlert } from "lucide-react";
import { type ComponentType, useState } from "react";

import { useFileCount } from "../api/client";
import type {
  AttemptRecord,
  Deliverable,
  LiveInfo,
  Project,
} from "../api/types";
import { TabBadge } from "../ui/count";
import { counted } from "../ui/format";
import { ChoiceMenu } from "../ui/menu";
import { NOT_RUN } from "../ui/status";
import { DeliverableCard } from "./DeliverableCard";
import FilesView from "./files";
import { type DeliverableProps, leading, reachedFirst } from "./kind";
import ReportView from "./report";
import TrialsView from "./trials";

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
}

interface Kind {
  View: ComponentType<DeliverableProps>;
  /** Show every deliverable of the kind, one under another, not a menu. */
  stack: boolean;
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

/** How many of a trials deliverable's k trials solved it, short and in full. */
function solved(item: Deliverable): { short: string; full: string } {
  if (item.path === null) return { short: NOT_RUN, full: NOT_RUN };
  const value = item.value?.value;
  const k = (item.k ?? 0).toString();
  if (typeof value === "number" || typeof value === "string")
    return {
      short: `${String(value)} / ${k}`,
      full: `${String(value)} of ${k} solved`,
    };
  return { short: `— / ${k}`, full: `Solved not known, of ${k}` };
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
    stack: false,
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
    stack: false,
    Count: ({ item, of }) => {
      const { short, full } = solved(item);
      return <TabBadge label={of + full}>{short}</TabBadge>;
    },
    Line: ({ item }) => `${solved(item).short} solved`,
  },
  report: {
    View: ReportView,
    stack: true,
    Count: ({ items }) => reachedBadge(items, "report"),
    Line: ({ item }) => (
      <code className="font-mono text-xs">{item.path?.split("/").at(-1)}</code>
    ),
  },
};

const unknown: Kind = {
  View: ({ deliverable }) => <DeliverableCard item={deliverable} />,
  stack: true,
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
  const { Count, stack } = kindOf(kind);
  const of = stack || title === props.item.title ? "" : `${props.item.title}: `;
  return <Count of={of} {...props} />;
}

/** A deliverable's line on its card in the Summary. */
export function KindLine({ item, ...props }: ItemProps) {
  const { Line } = kindOf(item.kind);
  return <Line item={item} {...props} />;
}

/** A kind's tab: its deliverables stacked, those reached first, or one chosen from a menu. */
export function KindTab({ kind, ...props }: TabProps & { kind: string }) {
  const { View, stack } = kindOf(kind);
  const [chosen, setChosen] = useState(
    () => leading(props.project, props.items)?.name,
  );
  const shown = stack
    ? reachedFirst(props.items)
    : props.items.filter((d) => d.name === chosen).slice(0, 1);
  return (
    <div className="flex flex-col gap-3 px-7 pt-5 pb-12">
      {!stack && props.items.length > 1 && (
        <ChoiceMenu
          items={props.items.map(choice)}
          chosen={shown[0] && choice(shown[0])}
          onChoose={setChosen}
        />
      )}
      {shown.map((deliverable) => (
        <View
          key={deliverable.name}
          project={props.project}
          attempt={props.attempt}
          record={props.record}
          deliverable={deliverable}
          live={props.live ?? null}
        />
      ))}
    </div>
  );
}

const choice = (item: Deliverable) => ({ key: item.name, title: item.title });
