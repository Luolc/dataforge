// A menu of the attempts of the item the page's attempt belongs to, each with
// when it started and its status, that opens the attempt that is picked.
import { ChevronDown } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { Link } from "react-router";

import type { AttemptRow, Project } from "../../api/types";
import { counted, time } from "../../ui/format";
import { StatusIcon, StatusLabel } from "../../ui/status-mark";
import { byNewestStart, rowStatus } from "../home/items";
import { attemptHref } from "./href";

/** The part of an attempt's ID after its item's. */
function nameOf(row: AttemptRow): string {
  const prefix = `${row.item}/`;
  return row.id.startsWith(prefix) ? row.id.slice(prefix.length) : row.id;
}

/** Nothing for an item with the attempt alone: there is nowhere to go. */
export function AttemptMenu({
  project,
  rows,
  current,
}: {
  project: Project;
  rows: AttemptRow[];
  current: AttemptRow;
}) {
  const same = byNewestStart(rows.filter((r) => r.item === current.item));
  if (same.length < 2) return null;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={`Attempts of this ${project.item.title.toLowerCase()}`}
        className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line bg-chip px-2 text-xs font-medium text-fg outline-none focus-visible:outline-2 focus-visible:outline-focus data-[state=open]:border-fg-3 [@media(hover:hover)_and_(pointer:fine)]:hover:border-fg-3"
      >
        {counted(same.length, "attempt")}
        <ChevronDown className="size-icon-sm text-icon" aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          className="flex max-h-96 flex-col gap-0.5 overflow-y-auto rounded-panel border border-line bg-panel p-1 text-xs text-fg"
        >
          {same.map((row) => (
            <DropdownMenu.Item key={row.id} asChild>
              <Link
                to={attemptHref(project.id, row.id)}
                aria-current={row.id === current.id ? "page" : undefined}
                className="flex target cursor-default items-center gap-3 rounded-row px-2 outline-none aria-[current=page]:font-medium aria-[current=page]:text-accent data-highlighted:bg-hover"
              >
                <StatusIcon project={project} word={rowStatus(row)} />
                <span className="font-mono">{nameOf(row)}</span>
                <span className="ml-auto flex items-center gap-3 text-fg-3 tabular-nums">
                  {row.started_at === null ? "—" : time(row.started_at)}
                  <StatusLabel project={project} word={rowStatus(row)} />
                </span>
              </Link>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
