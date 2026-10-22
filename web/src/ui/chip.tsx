// A filter's chip and the look of the small controls beside it.
import type { ReactNode } from "react";

/** The class of a small control in a row of filters, a chip's among them. */
export const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover";

/** A filter's choice, pressed while chosen, with how many it matches. */
export function Chip({
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
