// Every request the share site's frontend makes (design-share.md section 4,
// "The API"): sign in and out, a token's projects, a project's items and an
// item's record. The files of an item are read through urls.files, as the
// viewer's views read an attempt's.
import { createContext, use } from "react";
import { z } from "zod";

import { ApiError, getJson, type Loaded } from "../api/client";
import { project } from "../api/types";

/** A project as /api/projects serves it, from its export.json. */
const shareProject = project.pick({
  id: true,
  name: true,
  color: true,
  session: true,
  statuses: true,
});
export type ShareProject = z.infer<typeof shareProject>;
export const shareProjects = z.array(shareProject);

/** An item of a project: its ID and its revision, from 1. */
const listed = z.looseObject({ id: z.string(), revision: z.number() });
export type Listed = z.infer<typeof listed>;
export const listedItems = z.array(listed);

// A trial of an item's trials deliverable: an export writes no output and no
// rewards, and gives the trial's model.
const trial = z.looseObject({
  id: z.string(),
  path: z.string(),
  model: z.string().nullable().default(null),
  trace: z
    .looseObject({ path: z.string(), format: z.string().nullable() })
    .nullable(),
  result: z
    .looseObject({
      reward: z.number().nullable(),
      exception: z.string().nullable(),
    })
    .nullable(),
});
export type ShareTrial = z.infer<typeof trial>;

// A deliverable as an export writes it: of the shape of design.md's "The
// record", without the keys that say where in a run it was made.
const deliverable = z.looseObject({
  name: z.string(),
  kind: z.string(),
  title: z.string(),
  // A trials deliverable has none: each trial has its own.
  path: z.string().nullable().default(null),
  reason: z.string().nullable().default(null),
  k: z.number().optional(),
  value: z.looseObject({ value: z.unknown() }).nullable().optional(),
  trials: z.array(trial).optional(),
});
export type ShareDeliverable = z.infer<typeof deliverable>;

/** An item's record.json. */
export const itemRecord = z.looseObject({
  id: z.string(),
  revision: z.number(),
  deliverables: z.array(deliverable),
});

/** An item's ID as the site shows it and searches it: with its revision. */
export const shownId = (item: Listed) => `${item.id}-r${String(item.revision)}`;

const api = (p: string) => `/api/projects/${encodeURIComponent(p)}`;

export const shareUrls = {
  login: "/api/login",
  logout: "/api/logout",
  items: (p: string) => `${api(p)}/items`,
};

/** The URL of an item's page, with its tab when given. */
export function itemHref(p: string, item: string, tab?: string): string {
  const path = `/${encodeURIComponent(p)}/items/${encodeURIComponent(item)}`;
  return tab === undefined
    ? path
    : `${path}?${new URLSearchParams({ tab }).toString()}`;
}

/** What a sign-in came to: in, refused, or failed for another reason. */
export type SignIn =
  { state: "in" } | { state: "refused" } | { state: "failed"; message: string };

/**
 * Sign in with a token: the server sets the cookie that carries it. The
 * token goes in the body alone, never in a URL.
 */
export async function signIn(token: string): Promise<SignIn> {
  let response: Response;
  try {
    response = await fetch(shareUrls.login, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch (error) {
    return { state: "failed", message: messageOf(error) };
  }
  if (response.ok) return { state: "in" };
  // Every refusal, whatever its reason, is the same 401.
  if (response.status === 401) return { state: "refused" };
  return { state: "failed", message: response.status.toString() };
}

/** Sign out: the server clears the cookie. */
export async function signOut(): Promise<void> {
  await fetch(shareUrls.logout, { method: "POST" });
}

/** What the shell knows of the person: the projects their token names. */
export interface Signed {
  projects: Loaded<ShareProject[]>;
  /** Called when a read is refused: the token has expired or been revoked. */
  refused: () => void;
}

export const SignedContext = createContext<Signed>({
  projects: { state: "loading" },
  refused: () => undefined,
});

/** The project with an ID: loading, an error, or the project. */
export function useShareProject(id: string | undefined): Loaded<ShareProject> {
  const { projects } = use(SignedContext);
  if (projects.state !== "ok") return projects;
  const found = projects.data.find((p) => p.id === id);
  return found
    ? { state: "ok", data: found }
    : { state: "error", message: `No project ${id ?? ""}` };
}

/** Whether a read failed because the server refused the token. */
export const isRefused = (error: unknown) =>
  error instanceof ApiError && error.status === 401;

/**
 * GET a URL as getJson does; a 401 also tells the shell, which then asks
 * for a token again.
 */
export async function read<T>(
  url: string,
  schema: z.ZodType<T>,
  refused: () => void,
  signal?: AbortSignal,
): Promise<T> {
  try {
    return await getJson(url, schema, signal);
  } catch (error) {
    if (isRefused(error)) refused();
    throw error;
  }
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
