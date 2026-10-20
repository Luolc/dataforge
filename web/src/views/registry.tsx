// The only place a deliverable's kind picks what shows it: the view in its
// tab, whether several of the kind stack or one is chosen from a menu, the
// tab's count and one line on its card in the Summary.
import { ChevronDown, CircleAlert } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { type ComponentType, useState } from "react";

import { useFileCount } from "../api/client";
import type { AttemptRecord, Deliverable, Project } from "../api/types";
import { NOT_RUN } from "../ui/status";
import { DeliverableCard } from "./DeliverableCard";
import FilesView from "./files";
import type { DeliverableProps } from "./kind";
import ReportView from "./report";
import TrialsView from "./trials";

interface TabProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  items: Deliverable[];
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
    Count: ({ items }) => items.length,
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
export function kindOf(kind: string): Kind {
  return kinds[kind] ?? unknown;
}

/** A kind's tab: its deliverables stacked, or one chosen from a menu. */
export function KindTab({ kind, ...props }: TabProps & { kind: string }) {
  const { View, stack } = kindOf(kind);
  const [chosen, setChosen] = useState(props.items[0]?.name);
  const shown = stack
    ? props.items
    : props.items.filter((d) => d.name === chosen).slice(0, 1);
  return (
    <div className="flex flex-col gap-3 px-7 pt-5 pb-12">
      {!stack && props.items.length > 1 && (
        <Menu items={props.items} chosen={shown[0]} onChoose={setChosen} />
      )}
      {shown.map((deliverable) => (
        <View
          key={deliverable.name}
          project={props.project}
          attempt={props.attempt}
          record={props.record}
          deliverable={deliverable}
        />
      ))}
    </div>
  );
}

function Menu({
  items,
  chosen,
  onChoose,
}: {
  items: Deliverable[];
  chosen: Deliverable | undefined;
  onChoose: (name: string) => void;
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="target flex items-center gap-1.5 self-start rounded-row border border-line px-2.5 font-medium hover:bg-hover">
        {chosen?.title}
        <ChevronDown className="size-3 text-icon" aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          className="flex flex-col gap-0.5 rounded-panel border border-line bg-panel p-1"
        >
          {items.map((item) => (
            <DropdownMenu.Item
              key={item.name}
              onSelect={() => {
                onChoose(item.name);
              }}
              className="target flex cursor-default items-center rounded-row px-2 outline-none data-highlighted:bg-hover"
            >
              {item.title}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
