// The colour template switch at the right of the breadcrumb's bar.
import { Palette } from "lucide-react";
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
  return (
    <label className="ml-auto inline-flex target flex-none items-center gap-1.5 rounded-row border border-chip-line bg-chip px-2 text-xs text-fg-2 focus-within:border-fg-3">
      <Palette className="size-icon-sm text-icon" aria-hidden />
      <span className="sr-only">Colour template</span>
      <select
        value={chosen}
        onChange={(e) => {
          const v = e.target.value;
          if (!isTemplate(v)) return;
          setChosen(v);
          applyTemplate(v);
          try {
            localStorage.setItem(KEY, v);
          } catch {
            // Private mode: the choice lasts for this page only.
          }
        }}
        className="h-6 bg-transparent font-medium text-fg outline-none"
      >
        {TEMPLATES.map((t) => (
          <option key={t.id} value={t.id}>
            {t.label}
          </option>
        ))}
      </select>
    </label>
  );
}
