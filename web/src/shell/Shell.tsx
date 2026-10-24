// The frame of every page: the sidebar and an inset panel whose top bar
// holds the breadcrumb.
import {
  Activity,
  ChevronDown,
  ChevronRight,
  Diamond,
  ListOrdered,
} from "lucide-react";
import { Collapsible } from "radix-ui";
import type { ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { useJson, urls } from "../api/client";
import {
  type Page,
  type Project,
  projects as projectsSchema,
} from "../api/types";
import { firstPath, homePath, pagePath } from "./pages";
import { ProjectsContext } from "./projects";
import { TemplateSwitch } from "../ui/template-switch";
import { Sidebar, SidebarButton } from "./sidebar";

const navItem =
  "target flex min-h-row w-full items-center gap-2 rounded-row px-1.5 font-medium hover:bg-hover aria-[current=page]:bg-active";

export function Shell() {
  const projects = useJson(urls.projects(), projectsSchema);
  return (
    <ProjectsContext value={projects}>
      <Sidebar label="Site" page={<Outlet />}>
        <NavLink to="/monitor" className={navItem}>
          <Activity className="size-4 text-icon" aria-hidden />
          Monitor
        </NavLink>
        <NavLink to="/queue" className={navItem}>
          <ListOrdered className="size-4 text-icon" aria-hidden />
          Queue
        </NavLink>
        <Collapsible.Root defaultOpen className="mt-3">
          <Collapsible.Trigger className="group flex h-row target items-center gap-1 rounded-row px-1.5 text-xs font-medium text-fg-3 hover:bg-hover hover:text-fg-2">
            Projects
            {projects.state === "ok" && ` · ${projects.data.length}`}
            <ChevronDown
              className="size-2.5 transition-transform group-data-[state=closed]:-rotate-90 motion-reduce:transition-none"
              strokeWidth={2}
              aria-hidden
            />
          </Collapsible.Trigger>
          <Collapsible.Content>
            <ul className="mt-0.5 flex flex-col gap-0.5">
              {projects.state === "ok" &&
                projects.data.map((p) => (
                  <li key={p.id}>
                    {p.pages === undefined || p.pages.length === 0 ? (
                      <NavLink to={homePath(p.id)} className={navItem}>
                        <ProjectDot project={p} />
                        <span className="min-w-0 break-words">{p.name}</span>
                      </NavLink>
                    ) : (
                      <ProjectPages project={p} pages={p.pages} />
                    )}
                  </li>
                ))}
              {projects.state === "error" && (
                <li className="px-1.5 text-xs text-bad">{projects.message}</li>
              )}
            </ul>
          </Collapsible.Content>
        </Collapsible.Root>
      </Sidebar>
    </ProjectsContext>
  );
}

function ProjectDot({ project }: { project: Project }) {
  return (
    <Diamond
      className="size-3 flex-none fill-current"
      style={{ color: project.color }}
      aria-hidden
    />
  );
}

/**
 * A project that names pages: its name in bold, a link to its first page,
 * and its pages indented under it beside a line, the page shown in the
 * accent, the attempts page also while one of its attempts or its
 * Definition page is shown.
 */
function ProjectPages({ project, pages }: { project: Project; pages: Page[] }) {
  const { pathname } = useLocation();
  const home = homePath(project.id);
  const under = [`${home}/attempt`, `${home}/definition`];
  return (
    <>
      <Link to={firstPath(project)} className={`${navItem} font-semibold`}>
        <ProjectDot project={project} />
        <span className="min-w-0 break-words">{project.name}</span>
      </Link>
      <ul className="mt-0.5 ml-3 flex flex-col border-l border-line">
        {pages.map((page) => {
          const path = pagePath(project.id, page);
          const current =
            pathname === path
              ? "page"
              : page.kind === "attempts" && under.includes(pathname)
                ? "true"
                : undefined;
          return (
            <li key={page.name ?? page.kind}>
              <Link
                to={path}
                aria-current={current}
                className="-ml-px flex target min-h-row w-full items-center rounded-r-row pr-1.5 pl-3 text-faint hover:bg-hover hover:text-fg aria-[current=page]:text-accent aria-[current=page]:shadow-current aria-[current=true]:text-accent aria-[current=true]:shadow-current"
              >
                <span className="min-w-0 break-words">{page.title}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export interface Crumb {
  label: ReactNode;
  to?: string;
}

/** A page in the panel, under a breadcrumb whose last crumb is the page. */
export function Frame({
  crumbs,
  children,
}: {
  crumbs: Crumb[];
  children: ReactNode;
}) {
  return (
    <div className="fixed top-inset right-inset bottom-inset left-inset flex flex-col overflow-hidden rounded-panel border border-line bg-panel lg:left-sidebar">
      <nav
        aria-label="Breadcrumb"
        className="flex min-h-header flex-none items-center gap-2 border-b border-line py-1 pr-4 pl-3"
      >
        <SidebarButton />
        <ol className="flex min-w-0 flex-wrap items-center gap-0.5 font-medium">
          {crumbs.map((crumb, i) => (
            <li key={i} className="flex min-w-0 items-center gap-0.5">
              {i > 0 && (
                <ChevronRight
                  className="size-3 flex-none text-faint"
                  strokeWidth={2}
                  aria-hidden
                />
              )}
              {crumb.to !== undefined ? (
                <Link
                  to={crumb.to}
                  className="flex target min-w-0 items-center rounded-row px-1.5 wrap-anywhere link hover:underline"
                >
                  {crumb.label}
                </Link>
              ) : (
                <span
                  aria-current={i === crumbs.length - 1 ? "page" : undefined}
                  className="min-w-0 px-1.5 wrap-anywhere"
                >
                  {crumb.label}
                </span>
              )}
            </li>
          ))}
        </ol>
        <TemplateSwitch />
      </nav>
      <div className="min-h-0 flex-auto overflow-auto">{children}</div>
    </div>
  );
}
