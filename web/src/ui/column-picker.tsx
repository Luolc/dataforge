// The Columns button of a table's toolbar, and its panel: which of the
// table's columns show. It names how many are hidden, so that a reader knows
// the table is not whole.
import { Columns3, Eye, EyeOff, Lock, RotateCcw, Search } from "lucide-react";
import { Popover } from "radix-ui";
import { useState } from "react";

import { control } from "./chip";

export interface PickedColumn {
  key: string;
  title: string;
  /** Always shown: the reader cannot hide it. */
  locked?: boolean;
}

/**
 * A group's heading in the panel, with the button that moves all of it, when
 * there is anything to move.
 */
function GroupHeading({
  title,
  action,
  onAction,
}: {
  title: string;
  action: string;
  onAction: (() => void) | null;
}) {
  return (
    <div className="flex min-h-target items-center justify-between pl-2 text-2xs font-medium text-fg-3">
      <span>{title}</span>
      {onAction !== null && (
        <button
          type="button"
          onClick={onAction}
          className="target rounded-row px-1.5 hover:bg-hover hover:text-fg-2"
        >
          {action}
        </button>
      )}
    </div>
  );
}

/** A column's row in the panel: its title, a switch that shows or hides it. */
function ColumnRow({
  column,
  shown,
  onToggle,
}: {
  column: PickedColumn;
  shown: boolean;
  onToggle: () => void;
}) {
  const locked = column.locked === true;
  const Icon = locked ? Lock : shown ? Eye : EyeOff;
  return (
    <li>
      <button
        type="button"
        role="switch"
        aria-checked={shown}
        aria-label={column.title}
        disabled={locked}
        onClick={onToggle}
        className="flex target w-full items-center gap-2 rounded-row px-2 text-left text-xs hover:bg-hover disabled:hover:bg-transparent"
      >
        <span
          className={`flex-1 break-words ${shown ? "text-fg" : "text-fg-3"}`}
        >
          {column.title}
        </span>
        {locked && <span className="text-2xs text-fg-3">Always shown</span>}
        <Icon
          className={`size-icon-sm flex-none ${shown ? "text-icon" : "text-fg-3"}`}
          aria-hidden
        />
      </button>
    </li>
  );
}

/**
 * The button, with how many columns are hidden, and its panel: a search, the
 * columns shown and those hidden, each a switch, and a reset to show them
 * all. hidden is the keys of the columns hidden.
 */
export function ColumnPicker({
  columns,
  hidden,
  onChange,
}: {
  columns: readonly PickedColumn[];
  hidden: readonly string[];
  onChange: (hidden: string[]) => void;
}) {
  const [find, setFind] = useState("");
  const q = find.trim().toLowerCase();
  const matching = columns.filter((c) => c.title.toLowerCase().includes(q));
  const isHidden = (c: PickedColumn) => hidden.includes(c.key);
  const shown = matching.filter((c) => !isHidden(c));
  const hiddenShown = matching.filter(isHidden);
  // Hide all and Show all move every column, not only those the search finds.
  const hideable = columns.filter((c) => c.locked !== true && !isHidden(c));
  const n = columns.filter(isHidden).length;
  const toggle = (key: string) => {
    onChange(
      hidden.includes(key) ? hidden.filter((k) => k !== key) : [...hidden, key],
    );
  };
  return (
    <Popover.Root
      onOpenChange={(open) => {
        if (!open) setFind("");
      }}
    >
      <Popover.Trigger
        className={`${control} bg-chip data-[state=open]:border-fg-3`}
      >
        <Columns3 className="size-3.5 text-icon" aria-hidden />
        Columns
        {n > 0 && (
          // The space is for its name, the gap for its look.
          <span className="font-normal text-fg-2 tabular-nums">
            {` · ${n.toString()} hidden`}
          </span>
        )}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={4}
          aria-label="Columns"
          className="z-10 flex max-h-(--radix-popover-content-available-height) w-64 flex-col gap-1 overflow-y-auto rounded-panel border border-line bg-panel p-1.5 text-fg"
        >
          <label className="flex target items-center gap-1.5 rounded-row border border-chip-line px-2 focus-within:border-fg-3">
            <Search className="size-3.5 flex-none text-icon" aria-hidden />
            <input
              type="search"
              aria-label="Find a column"
              placeholder="Find a column"
              value={find}
              onChange={(e) => {
                setFind(e.target.value);
              }}
              className="h-6 w-full bg-transparent text-xs outline-none placeholder:text-fg-3"
            />
          </label>
          {shown.length > 0 && (
            <div role="group" aria-label="Shown in the table" className="pt-1">
              <GroupHeading
                title="Shown in the table"
                action="Hide all"
                onAction={
                  hideable.length === 0
                    ? null
                    : () => {
                        onChange([...hidden, ...hideable.map((c) => c.key)]);
                      }
                }
              />
              <ul className="flex flex-col">
                {shown.map((c) => (
                  <ColumnRow
                    key={c.key}
                    column={c}
                    shown
                    onToggle={() => {
                      toggle(c.key);
                    }}
                  />
                ))}
              </ul>
            </div>
          )}
          {hiddenShown.length > 0 && (
            <div
              role="group"
              aria-label="Hidden"
              className="border-t border-line-subtle pt-1"
            >
              <GroupHeading
                title="Hidden"
                action="Show all"
                onAction={() => {
                  onChange([]);
                }}
              />
              <ul className="flex flex-col">
                {hiddenShown.map((c) => (
                  <ColumnRow
                    key={c.key}
                    column={c}
                    shown={false}
                    onToggle={() => {
                      toggle(c.key);
                    }}
                  />
                ))}
              </ul>
            </div>
          )}
          {matching.length === 0 && (
            <p className="px-2 py-1 text-xs text-fg-3">No column matches.</p>
          )}
          <div className="border-t border-line-subtle pt-1">
            <button
              type="button"
              disabled={n === 0}
              onClick={() => {
                onChange([]);
              }}
              className="flex target w-full items-center gap-2 rounded-row px-2 text-xs text-fg-2 hover:bg-hover disabled:text-fg-3 disabled:hover:bg-transparent"
            >
              <RotateCcw className="size-3.5 text-icon" aria-hidden />
              Reset to default
            </button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
