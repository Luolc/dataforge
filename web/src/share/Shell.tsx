// The share site's frame: before a sign-in the landing page alone; after it
// a sidebar with the token's projects, and the page in the viewer's panel.
import { ChevronDown, Diamond, LogOut } from "lucide-react";
import { Collapsible } from "radix-ui";
import { useCallback, useMemo, useState } from "react";
import { NavLink, Outlet } from "react-router";

import { getJson, type Loaded, urls, useLoaded } from "../api/client";
import { toneGlyph, toneText } from "../ui/status";
import {
  isRefused,
  type ShareProject,
  shareProjects,
  SignedContext,
  signOut,
} from "./api";
import { Landing } from "./Landing";

const BadGlyph = toneGlyph.bad;

const navItem =
  "target flex min-h-row w-full items-center gap-2 rounded-row px-1.5 font-medium hover:bg-hover aria-[current=page]:bg-active";

/** The token's projects; null when the server refuses the token. */
async function readProjects(signal: AbortSignal) {
  try {
    return await getJson(urls.projects(), shareProjects, signal);
  } catch (error) {
    if (isRefused(error)) return null;
    throw error;
  }
}

export function ShareShell() {
  // Each sign-in, sign-out and refusal reads the projects again from the
  // start, so nothing read with one token shows with another.
  const [generation, setGeneration] = useState(0);
  const again = useCallback(() => {
    setGeneration((g) => g + 1);
  }, []);
  const read = useLoaded(`projects:${String(generation)}`, readProjects);
  const [signOutFailed, setSignOutFailed] = useState<string | null>(null);
  const signed = useMemo(() => {
    const projects: Loaded<ShareProject[]> =
      read.state === "ok" ? { state: "ok", data: read.data ?? [] } : read;
    return { projects, refused: again };
  }, [read, again]);
  if (read.state === "loading") return null;
  if (read.state === "ok" && read.data === null)
    return <Landing onSignedIn={again} />;
  const { projects } = signed;
  return (
    <SignedContext value={signed}>
      <nav
        aria-label="Site"
        className="fixed inset-y-0 left-0 flex w-sidebar flex-col gap-0.5 px-2 py-inset"
      >
        <Collapsible.Root defaultOpen>
          <Collapsible.Trigger className="group flex h-row target items-center gap-1 rounded-row px-1.5 text-xs font-medium text-fg-3 hover:bg-hover hover:text-fg-2">
            Projects
            {projects.state === "ok" && ` · ${String(projects.data.length)}`}
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
                        className="size-3 fill-current"
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
        {signOutFailed !== null && (
          <p
            role="alert"
            className={`mt-auto flex items-start gap-1.5 px-1.5 text-xs ${toneText.bad}`}
          >
            <BadGlyph className="mt-px size-3.5 flex-none" aria-hidden />
            {`Signing out failed: ${signOutFailed}. You are still signed in.`}
          </p>
        )}
        <button
          type="button"
          onClick={() => {
            void signOut().then((failed) => {
              setSignOutFailed(failed);
              if (failed === null) again();
            });
          }}
          className={`${signOutFailed === null ? "mt-auto" : ""} text-fg-2 ${navItem}`}
        >
          <LogOut className="size-4 text-icon" aria-hidden />
          Sign out
        </button>
      </nav>
      <Outlet />
    </SignedContext>
  );
}
