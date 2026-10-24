// A page's row of tabs, each a link with its count beside its title, shared
// by the attempt page and the share site's item page.
import type { ReactNode } from "react";
import { Link } from "react-router";

export interface NavTab {
  key: string;
  title: string;
  count?: ReactNode;
  /** The URL the tab opens. */
  to: string;
}

export function TabNav({
  label,
  tabs,
  current,
}: {
  label: string;
  tabs: NavTab[];
  current: string | undefined;
}) {
  return (
    <nav
      aria-label={label}
      className="sticky top-0 z-10 mt-3 flex h-tabs gap-1 border-b border-line bg-panel px-5"
    >
      {tabs.map((t) => (
        <Link
          key={t.key}
          to={t.to}
          aria-current={t.key === current ? "page" : undefined}
          className="relative flex target flex-none items-center gap-2 px-3 font-medium whitespace-nowrap text-fg-2 hover:text-fg aria-[current=page]:text-fg aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-1.5 aria-[current=page]:after:-bottom-px aria-[current=page]:after:h-0.5 aria-[current=page]:after:rounded-full aria-[current=page]:after:bg-accent"
        >
          {t.title}
          {t.count}
        </Link>
      ))}
    </nav>
  );
}
