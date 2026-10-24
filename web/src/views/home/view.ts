// The home page's view a reader sets, kept in localStorage per project as
// {"version": 1, "hidden": [<column key>, ...], "rules": [...]}: here, which
// columns it hides. Fields this page does not read, the rules among them,
// stay as they are kept.

/** Where a project's view is kept in localStorage. */
export const viewKey = (project: string) => `viz.home.${project}`;

/** The key of a project column in the view: by its title. */
export const columnKey = (title: string) => `col.${title}`;

const VERSION = 1;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** What is kept for the project, when it is a view of this version; else null. */
function kept(project: string): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(viewKey(project));
    if (raw === null) return null;
    const view: unknown = JSON.parse(raw);
    return isObject(view) && view["version"] === VERSION ? view : null;
  } catch {
    return null;
  }
}

/**
 * The columns kept hidden for the project, of those keys names, in their
 * order: a column its project file no longer has is dropped.
 */
export function loadHidden(project: string, keys: readonly string[]): string[] {
  const hidden = kept(project)?.["hidden"];
  return Array.isArray(hidden) ? keys.filter((k) => hidden.includes(k)) : [];
}

/** Keeps the columns hidden for the project, and the rest of its view. */
export function saveHidden(project: string, hidden: readonly string[]) {
  try {
    localStorage.setItem(
      viewKey(project),
      JSON.stringify({ ...kept(project), version: VERSION, hidden }),
    );
  } catch {
    // Private mode: the view lasts for this page only.
  }
}
