// The colour templates: each is one set of values for the same tokens
// (theme.css), chosen on the page and kept in localStorage.
export const TEMPLATES = [
  { id: "ac", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "contrast", label: "High contrast" },
  { id: "neutral", label: "Neutral light" },
] as const;

export type TemplateId = (typeof TEMPLATES)[number]["id"];

/** Where the chosen template is kept in localStorage. */
export const KEY = "viz.template";
const DEFAULT: TemplateId = "ac";

export const isTemplate = (v: string | null): v is TemplateId =>
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
