// The colour templates: each is one set of values for the same tokens
// (theme.css), chosen on the page and kept in localStorage.
import { Palette } from "lucide-react";
import { useState } from "react";

export const TEMPLATES = [
  { id: "contrast", label: "High contrast" },
  { id: "ac", label: "AC layered" },
  { id: "neutral", label: "Neutral light" },
  { id: "dark", label: "Dark" },
] as const;

type TemplateId = (typeof TEMPLATES)[number]["id"];

const KEY = "viz.template";
const DEFAULT: TemplateId = "contrast";

const isTemplate = (v: string | null): v is TemplateId =>
  TEMPLATES.some((t) => t.id === v);

/** The template kept in localStorage, else the default. */
export function savedTemplate(): TemplateId {
  try {
    const v = localStorage.getItem(KEY);
    return isTemplate(v) ? v : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

/** Draws the page in a template: theme.css keys its tokens on this. */
export function applyTemplate(id: TemplateId) {
  document.documentElement.dataset["template"] = id;
}

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
