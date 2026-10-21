// The share site's frame: a sidebar with the projects, and the page in the
// viewer's panel. With a token, the landing page alone until a sign-in.
import { ChevronDown, Diamond, LogOut } from "lucide-react";
import { Collapsible } from "radix-ui";
import { useCallback, useMemo, useState } from "react";
import { NavLink, Outlet } from "react-router";

import { getJson, type Loaded, useLoaded } from "../api/client";
import { toneGlyph, toneText } from "../ui/status";
import {
  isRefused,
  type ShareProject,
  shareProjects,
  SignedContext,
  type TokenAccess,
  useShareSource,
} from "./api";
import { Sidebar } from "../shell/sidebar";

const BadGlyph = toneGlyph.bad;

const navItem =
  "target flex min-h-row w-full items-center gap-2 rounded-row px-1.5 font-medium hover:bg-hover aria-[current=page]:bg-active";

/** The projects; null when the server refuses a token, with access by one. */
async function readProjects(url: string, token: boolean, signal: AbortSignal) {
  try {
    return await getJson(url, shareProjects, signal);
  } catch (error) {
    if (token && isRefused(error)) return null;
    throw error;
  }
}

export function ShareShell() {
  const source = useShareSource();
  const { access } = source;
  // Each sign-in, sign-out and refusal reads the projects again from the
  // start, so nothing read with one token shows with another.
  const [generation, setGeneration] = useState(0);
  const again = useCallback(() => {
    setGeneration((g) => g + 1);
  }, []);
  const read = useLoaded(`projects:${String(generation)}`, (signal) =>
    readProjects(source.projects, access.kind === "token", signal),
  );
  const signed = useMemo(() => {
    const projects: Loaded<ShareProject[]> =
      read.state === "ok" ? { state: "ok", data: read.data ?? [] } : read;
    return { projects, refused: again };
  }, [read, again]);
  if (read.state === "loading") return null;
  if (read.state === "ok" && read.data === null && access.kind === "token")
    return <access.Landing onSignedIn={again} />;
  const { projects } = signed;
  return (
    <SignedContext value={signed}>
      <Sidebar label="Site" page={<Outlet />}>
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
        {access.kind === "token" ? (
          <SignOut access={access} signedOut={again} />
        ) : (
          <a href={access.logout} className={`mt-auto text-fg-2 ${navItem}`}>
            <LogOut className="size-4 text-icon" aria-hidden />
            Sign out
          </a>
        )}
      </Sidebar>
    </SignedContext>
  );
}

/** Sign out of the server, or say why it failed and stay signed in. */
function SignOut({
  access,
  signedOut,
}: {
  access: TokenAccess;
  signedOut: () => void;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  return (
    <>
      {failed !== null && (
        <p
          role="alert"
          className={`mt-auto flex items-start gap-1.5 px-1.5 text-xs ${toneText.bad}`}
        >
          <BadGlyph className="mt-px size-3.5 flex-none" aria-hidden />
          {`Signing out failed: ${failed}. You are still signed in.`}
        </p>
      )}
      <button
        type="button"
        onClick={() => {
          void access.signOut().then((reason) => {
            setFailed(reason);
            if (reason === null) signedOut();
          });
        }}
        className={`${failed === null ? "mt-auto" : ""} text-fg-2 ${navItem}`}
      >
        <LogOut className="size-4 text-icon" aria-hidden />
        Sign out
      </button>
    </>
  );
}
