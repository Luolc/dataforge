// A hint: a line that tells more about something on a page, on hover and on
// focus, in a tooltip.
import { Tooltip } from "radix-ui";
import type { ReactNode } from "react";

/** What a hint tells, beside the trigger it opens from. */
export function HintContent({ children }: { children: ReactNode }) {
  return (
    <Tooltip.Portal>
      <Tooltip.Content
        side="bottom"
        align="start"
        sideOffset={4}
        className="z-10 max-w-96 rounded-row border border-line bg-active px-2.5 py-1.5 text-left text-xs font-normal break-words text-fg"
      >
        {children}
      </Tooltip.Content>
    </Tooltip.Portal>
  );
}

/**
 * Text with a hint, opened on hover and on focus, so a keyboard opens it too;
 * inside a Tooltip.Provider.
 */
export function Hint({
  hint,
  children,
}: {
  hint: string;
  children: ReactNode;
}) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex -- the tooltip opens on focus, so a keyboard can reach it */}
        <span tabIndex={0} className="inline-flex rounded-row">
          {children}
        </span>
      </Tooltip.Trigger>
      <HintContent>{hint}</HintContent>
    </Tooltip.Root>
  );
}
