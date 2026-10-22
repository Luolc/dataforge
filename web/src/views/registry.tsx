// The only place a deliverable's kind picks what shows it: the view in its
// tab, whether several of the kind stack or one is chosen from a menu, the
// tab's count and one line on its card in the Summary.
import { CircleAlert } from "lucide-react";
import { type ComponentType } from "react";
import { useSearchParams } from "react-router";

import { useFileCount } from "../api/client";
import type {
  AttemptRecord,
  Deliverable,
  LiveInfo,
  Project,
} from "../api/types";
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
  /** The tab's count. */
  Count: ComponentType<ItemProps & { items: Deliverable[] }>;
  /** One line on a deliverable's card, when its path was reached. */
  Line: ComponentType<ItemProps>;
}

/** How many files are under a deliverable's path; nothing while loading. */
function FileCount({ project, attempt, item }: ItemProps) {
  const count = useFileCount(project, attempt, item.path);
  if (count.state === "loading") return null;
  if (count.state === "error") {
    return (
      <CircleAlert
        role="img"
        aria-label={`Could not count the files: ${count.message}`}
        className="inline size-3 text-bad"
      />
    );
  }
  return <>{`${count.data.files}${count.data.complete ? "" : "+"}`}</>;
}

function solved(item: Deliverable): string {
  if (item.path === null) return NOT_RUN;
  const value = item.value?.value;
  const shown =
    typeof value === "number" || typeof value === "string"
      ? String(value)
      : "—";
  return `${shown} / ${item.k ?? 0}`;
}

const kinds: Partial<Record<string, Kind>> = {
  files: {
    View: FilesView,
    stack: false,
    Count: (props) =>
      props.item.path === null ? NOT_RUN : <FileCount {...props} />,
    Line: (props) => (
      <>
        <FileCount {...props} /> files
      </>
    ),
  },
  trials: {
    View: TrialsView,
    stack: false,
    Count: ({ item }) => solved(item),
    Line: ({ item }) => `${solved(item)} solved`,
  },
  report: {
    View: ReportView,
    stack: true,
    Count: ({ items }) => {
      const reached = items.filter((item) => item.path !== null).length;
      return reached === 0 ? NOT_RUN : reached;
    },
    Line: ({ item }) => (
      <code className="font-mono text-xs">{item.path?.split("/").at(-1)}</code>
    ),
  },
};

const unknown: Kind = {
  View: ({ deliverable }) => <DeliverableCard item={deliverable} />,
  stack: true,
  Count: ({ items }) => items.length,
  Line: ({ item }) => <code className="font-mono text-xs">{item.kind}</code>,
};

/** What shows deliverables of a kind; an unknown kind shows as cards. */
function kindOf(kind: string): Kind {
  return kinds[kind] ?? unknown;
}

/** The count on a kind's tab. */
export function KindCount({
  kind,
  ...props
}: ItemProps & { items: Deliverable[]; kind: string }) {
  const { Count } = kindOf(kind);
  return <Count {...props} />;
}

/** A deliverable's line on its card in the Summary. */
export function KindLine({ item, ...props }: ItemProps) {
  const { Line } = kindOf(item.kind);
  return <Line item={item} {...props} />;
}

/**
 * A kind's tab: its deliverables stacked, those reached first, or one chosen
 * from a menu, the one the page's `deliverable` names, else the leading one.
 */
export function KindTab({ kind, ...props }: TabProps & { kind: string }) {
  const { View, stack } = kindOf(kind);
  const [query, setQuery] = useSearchParams();
  const asked = props.items.find((d) => d.name === query.get("deliverable"));
  const chosen = (asked ?? leading(props.items))?.name;
  const setChosen = (name: string) => {
    setQuery((before) => {
      const next = new URLSearchParams(before);
      next.set("deliverable", name);
      // What else the page shows belongs to the deliverable it leaves.
      next.delete("trial");
      next.delete("file");
      return next;
    });
  };
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
