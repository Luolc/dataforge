// A menu that picks one of a few things shown in the same place, such as one
// deliverable of a kind or one version of a tab.
import { ChevronDown } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import type { ReactNode } from "react";

export interface Choice {
  key: string;
  title: string;
  /** Shown after the title in the menu, such as a count. */
  aside?: ReactNode;
}

/** The chosen item's title as a button that opens a menu of all of them. */
export function ChoiceMenu({
  items,
  chosen,
  onChoose,
  label,
}: {
  items: Choice[];
  chosen: Choice | undefined;
  onChoose: (key: string) => void;
  /** What the items are, read before the chosen one's title. */
  label?: string;
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={
          label === undefined ? undefined : `${label}: ${chosen?.title ?? ""}`
        }
        className="flex target items-center gap-1.5 self-start rounded-row border border-line px-2.5 font-medium hover:bg-hover"
      >
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
              key={item.key}
              onSelect={() => {
                onChoose(item.key);
              }}
              className="flex target cursor-default items-center justify-between gap-4 rounded-row px-2 outline-none data-highlighted:bg-hover"
            >
              {item.title}
              {item.aside !== undefined && (
                <span className="text-xs text-fg-3 tabular-nums">
                  {/* So a screen reader does not run the two together. */}
                  <span className="sr-only">,</span> {item.aside}
                </span>
              )}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
