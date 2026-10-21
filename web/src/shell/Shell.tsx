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
import { Link, NavLink, Outlet } from "react-router";

import { useJson, urls } from "../api/client";
import { projects as projectsSchema } from "../api/types";
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
                    <NavLink
                      to={`/${encodeURIComponent(p.id)}`}
                      className={navItem}
                    >
                      <Diamond
                        className="size-3 flex-none fill-current"
                        style={{ color: p.color }}
                        aria-hidden
                      />
                      <span className="min-w-0 break-words">{p.name}</span>
                    </NavLink>
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
