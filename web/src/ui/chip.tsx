// A filter's chip and the look of the small controls beside it.
import { Tooltip } from "radix-ui";
import type { ReactNode } from "react";

import { HintContent } from "./hint";

/** The class of a small control in a row of filters, a chip's among them. */
export const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover";

/**
 * A filter's choice, pressed while chosen, with how many it matches; hint,
 * what the count counts, on hover and focus, in a Tooltip.Provider.
 */
export function Chip({
  pressed,
  onClick,
  icon,
  label,
  count,
  hint,
}: {
  pressed: boolean;
  onClick: () => void;
  icon?: ReactNode;
  label: string;
  count: number;
  hint?: string;
}) {
  const chip = (
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
  if (hint === undefined) return chip;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{chip}</Tooltip.Trigger>
      <HintContent>{hint}</HintContent>
    </Tooltip.Root>
  );
}
