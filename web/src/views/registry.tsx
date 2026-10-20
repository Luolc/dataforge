// The only place a deliverable's kind picks what shows it: a view for its tab,
// a count for the tab and one line for its card on the Summary.
import { CircleAlert } from "lucide-react";
import type { ComponentType } from "react";

import { useFileCount } from "../api/client";
import type { Deliverable } from "../api/types";
import { DeliverableCard } from "./DeliverableCard";
import { FilesView } from "./files/FilesView";
import type { KindProps } from "./kind";
import { ReportView } from "./report/ReportView";
import { TrialsView } from "./trials/TrialsView";

interface ItemProps {
  project: string;
  attempt: string;
  item: Deliverable;
}

interface Kind {
  View: ComponentType<KindProps>;
  /** The tab's count, from the kind's first deliverable or all of them. */
  Count: ComponentType<KindProps>;
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
  const value = item.value?.value;
  const shown =
    typeof value === "number" || typeof value === "string"
      ? String(value)
      : "?";
  return `${shown} / ${item.k ?? 0}`;
}

const kinds: Partial<Record<string, Kind>> = {
  files: {
    View: FilesView,
    Count: ({ project, attempt, items }) =>
      items[0] ? (
        <FileCount project={project.id} attempt={attempt} item={items[0]} />
      ) : null,
    Line: (props) => (
      <>
        <FileCount {...props} /> files
      </>
    ),
  },
  trials: {
    View: TrialsView,
    Count: ({ items }) => (items[0] ? solved(items[0]) : null),
    Line: ({ item }) => `${solved(item)} solved`,
  },
  report: {
    View: ReportView,
    Count: ({ items }) => items.length,
    Line: ({ item }) => (
      <code className="font-mono text-xs">{item.path?.split("/").at(-1)}</code>
    ),
  },
};

function UnknownKind({ items }: KindProps) {
  return (
    <div className="flex flex-col gap-3 p-7">
      {items.map((item) => (
        <DeliverableCard key={item.name} item={item} />
      ))}
    </div>
  );
}

const unknown: Kind = {
  View: UnknownKind,
  Count: ({ items }) => items.length,
  Line: ({ item }) => <code className="font-mono text-xs">{item.kind}</code>,
};

/** What shows deliverables of a kind; an unknown kind shows as cards. */
export function kindOf(kind: string): Kind {
  return kinds[kind] ?? unknown;
}

/** The tab of one kind: its deliverables, shown by the kind's view. */
export function KindView(props: KindProps & { kind: string }) {
  const { View } = kindOf(props.kind);
  return <View {...props} />;
}
