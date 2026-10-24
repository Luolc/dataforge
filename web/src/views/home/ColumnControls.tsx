// The headings of a list's columns, each a button that sorts by it, and the
// chips of a column that filters, shared by the home page and the share site.
import { ArrowDown, ArrowUp } from "lucide-react";
import { Tooltip } from "radix-ui";
import type { ReactNode } from "react";

import type { Column } from "../../api/types";
import { Chip } from "../../ui/chip";
import { counted } from "../../ui/format";
import { HintContent } from "../../ui/hint";
import { DOTTED, type Spec } from "./columns";
import type { ColumnChips } from "./items";

/** A heading's button, with what its column holds on hover and focus. */
function Described({
  description,
  children,
}: {
  description: string | null | undefined;
  children: ReactNode;
}) {
  if (description == null) return children;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <HintContent>{description}</HintContent>
    </Tooltip.Root>
  );
}

/** A column's heading, a button that sorts by it, in a Tooltip.Provider. */
export function SortHeading({
  spec,
  sorted,
  onSort,
  className = "",
}: {
  spec: Pick<Spec<never>, "title" | "numeric" | "description"> | undefined;
  sorted: false | "asc" | "desc";
  onSort: ((event: unknown) => void) | undefined;
  className?: string;
}) {
  const Arrow = sorted === "desc" ? ArrowDown : ArrowUp;
  return (
    <th
      scope="col"
      aria-sort={
        sorted === "asc"
          ? "ascending"
          : sorted === "desc"
            ? "descending"
            : "none"
      }
      className={`px-3 py-1 font-medium ${spec?.numeric === true ? "text-right" : "text-left"} ${className}`}
    >
      <Described description={spec?.description}>
        <button
          type="button"
          onClick={onSort}
          className={`inline-flex target items-center gap-1 rounded-row hover:text-fg-2 ${spec?.numeric === true ? "flex-row-reverse" : ""}`}
        >
          {/* Dotted, as a hint's sign, when it has one. */}
          <span className={spec?.description == null ? "" : DOTTED}>
            {spec?.title}
          </span>
          {sorted !== false && <Arrow className="size-3" aria-hidden />}
        </button>
      </Described>
    </th>
  );
}

/**
 * The chips that filter by a column's value: All, then one per value, each
 * with how many items (`unit`) it keeps; in a Tooltip.Provider.
 */
export function ColumnFilter({
  column,
  chips: { all, chips },
  picked,
  onPick,
  unit,
}: {
  column: Column;
  chips: ColumnChips;
  /** The value chosen, null for All. */
  picked: string | null;
  onPick: (key: string | null) => void;
  unit: string;
}) {
  return (
    <div
      role="group"
      aria-label={`Filter by ${column.title}`}
      className="flex flex-wrap items-center gap-1.5"
    >
      {/* Names the group, whose All would read as the status filter's. */}
      <span aria-hidden className="text-xs text-fg-3">
        {column.title}
      </span>
      <Chip
        pressed={picked === null}
        onClick={() => {
          onPick(null);
        }}
        label="All"
        count={all}
        hint={counted(all, unit)}
      />
      {chips.map(({ key, count }) => (
        <Chip
          key={key}
          pressed={picked === key}
          onClick={() => {
            onPick(picked === key ? null : key);
          }}
          label={column.labels?.[key] ?? key}
          count={count}
          hint={[column.hints?.[key], counted(count, unit)]
            .filter((h) => h !== undefined)
            .join(" ")}
        />
      ))}
    </div>
  );
}
