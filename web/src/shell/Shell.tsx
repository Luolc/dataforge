// The frame of every page: a fixed sidebar and an inset panel whose top bar
// holds the breadcrumb.
import { ChevronDown, ChevronRight, Diamond, Activity } from "lucide-react";
import { Collapsible } from "radix-ui";
import type { ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router";

import { useJson, urls } from "../api/client";
import { projects as projectsSchema } from "../api/types";
import { ProjectsContext } from "./projects";

const navItem =
  "flex h-row w-full items-center gap-2 rounded-row px-1.5 font-medium whitespace-nowrap hover:bg-hover aria-[current=page]:bg-active";

export function Shell() {
  const projects = useJson(urls.projects(), projectsSchema);
  return (
    <ProjectsContext value={projects}>
      <nav
        aria-label="Site"
        className="fixed inset-y-0 left-0 flex w-sidebar flex-col gap-0.5 px-2 py-inset"
      >
        <NavLink to="/monitor" className={navItem}>
          <Activity className="size-4 text-icon" aria-hidden />
          Monitor
        </NavLink>
        <Collapsible.Root defaultOpen className="mt-3">
          <Collapsible.Trigger className="group flex h-row items-center gap-1 rounded-row px-1.5 text-xs font-medium text-fg-3 hover:bg-hover hover:text-fg-2">
            Projects
            <ChevronDown
              className="size-2.5 transition-transform group-data-[state=closed]:-rotate-90"
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
                        className="size-3 fill-current"
                        style={{ color: p.color }}
                        aria-hidden
                      />
                      <span className="truncate">{p.name}</span>
                    </NavLink>
                  </li>
                ))}
              {projects.state === "error" && (
                <li className="px-1.5 text-xs text-bad">{projects.message}</li>
              )}
            </ul>
          </Collapsible.Content>
        </Collapsible.Root>
      </nav>
      <Outlet />
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
    <div className="fixed top-inset right-inset bottom-inset left-sidebar flex flex-col overflow-hidden rounded-panel border border-line bg-panel">
      <nav
        aria-label="Breadcrumb"
        className="flex h-header flex-none items-center border-b border-line pr-4 pl-3"
      >
        <ol className="flex min-w-0 items-center gap-0.5 font-medium">
          {crumbs.map((crumb, i) => (
            <li key={i} className="flex min-w-0 items-center gap-0.5">
              {i > 0 && (
                <ChevronRight
                  className="size-3 flex-none text-faint"
                  strokeWidth={2}
                  aria-hidden
                />
              )}
              {crumb.to !== undefined && i < crumbs.length - 1 ? (
                <Link
                  to={crumb.to}
                  className="flex h-6 items-center rounded-row px-1.5 whitespace-nowrap text-fg-2 hover:bg-hover hover:text-fg"
                >
                  {crumb.label}
                </Link>
              ) : (
                <span
                  aria-current={i === crumbs.length - 1 ? "page" : undefined}
                  className="truncate px-1.5"
                >
                  {crumb.label}
                </span>
              )}
            </li>
          ))}
        </ol>
      </nav>
      <div className="min-h-0 flex-auto overflow-auto">{children}</div>
    </div>
  );
}
