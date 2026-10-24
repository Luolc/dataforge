// A project's pages under its entry in the sidebar (design-viz.md section 4,
// "The project file"), and the breadcrumb's crumbs for them.
import type { Loaded } from "../api/client";
import type { Page, Project } from "../api/types";
import type { Crumb } from "./Shell";

/** The path of a project's home page, its attempts. */
export const homePath = (id: string) => `/${encodeURIComponent(id)}`;

/** The path of one of a project's pages. */
export function pagePath(id: string, page: Page): string {
  return page.kind === "attempts" || page.name === null
    ? homePath(id)
    : `${homePath(id)}/${encodeURIComponent(page.name)}`;
}

/** The path the project's entry opens: its first page, else its home page. */
export function firstPath(project: Pick<Project, "id" | "pages">): string {
  const first = project.pages?.[0];
  return first === undefined
    ? homePath(project.id)
    : pagePath(project.id, first);
}

/** The project's attempts page, when it names pages. */
export function attemptsPage(
  project: Pick<Project, "pages">,
): Page | undefined {
  return project.pages?.find((p) => p.kind === "attempts");
}

/** A snapshot page of the project by its name. */
export function snapshotPage(
  project: Pick<Project, "pages">,
  name: string | undefined,
): Page | undefined {
  return project.pages?.find((p) => p.kind === "snapshot" && p.name === name);
}

/**
 * The crumbs of a page of the project: Projects, the project's name and,
 * when it names pages, the title of page; each a link to its page but the
 * last, the page itself, when it is the page shown.
 */
export function projectCrumbs(
  project: Loaded<Project>,
  id: string | undefined,
  page: (p: Project) => Page | undefined,
  shown: boolean,
): Crumb[] {
  const projects: Crumb = { label: "Projects", to: "/" };
  if (project.state !== "ok")
    return [
      projects,
      shown ? { label: id ?? "" } : { label: id ?? "", to: homePath(id ?? "") },
    ];
  const found = page(project.data);
  const name: Crumb = {
    label: project.data.name,
    to: firstPath(project.data),
  };
  if (found === undefined)
    return [projects, shown ? { label: project.data.name } : name];
  return [
    projects,
    name,
    shown
      ? { label: found.title }
      : { label: found.title, to: pagePath(project.data.id, found) },
  ];
}
