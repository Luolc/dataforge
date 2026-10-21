// The colour template switch at the right of the breadcrumb's bar: a button
// that opens a menu of the templates, drawn like the site's other menus.
import { Check, ChevronDown, Palette } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useState } from "react";

import {
  applyTemplate,
  isTemplate,
  KEY,
  savedTemplate,
  TEMPLATES,
} from "./template";

/** The switch at the top right of every page. */
export function TemplateSwitch() {
  const [chosen, setChosen] = useState(savedTemplate);
  const label = TEMPLATES.find((t) => t.id === chosen)?.label;
  const choose = (v: string) => {
    if (!isTemplate(v)) return;
    setChosen(v);
    applyTemplate(v);
    try {
      localStorage.setItem(KEY, v);
    } catch {
      // Private mode: the choice lasts for this page only.
    }
  };
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={`Colour template: ${label ?? ""}`}
        className="ml-auto inline-flex target flex-none items-center gap-1.5 rounded-row border border-chip-line bg-chip px-2 text-xs font-medium text-fg outline-none hover:border-fg-3 focus-visible:outline-2 focus-visible:outline-focus data-[state=open]:border-fg-3"
      >
        <Palette className="size-icon-sm text-icon" aria-hidden />
        {label}
        <ChevronDown className="size-icon-sm text-icon" aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={4}
          aria-label="Colour template"
          className="flex min-w-40 flex-col gap-0.5 rounded-panel border border-line bg-panel p-1 text-xs text-fg"
        >
          <DropdownMenu.RadioGroup value={chosen} onValueChange={choose}>
            {TEMPLATES.map((t) => (
              <DropdownMenu.RadioItem
                key={t.id}
                value={t.id}
                className="flex target cursor-default items-center gap-2 rounded-row px-2 outline-none data-highlighted:bg-hover data-[state=checked]:font-medium data-[state=checked]:text-accent"
              >
                <span className="flex-1">{t.label}</span>
                <DropdownMenu.ItemIndicator>
                  <Check className="size-icon-sm" aria-hidden />
                </DropdownMenu.ItemIndicator>
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
