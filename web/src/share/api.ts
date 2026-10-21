// What the share site's pages read, and the source they read it from: the
// share server's API (api-source.ts) or the files of a static site
// (static-source.ts). The files of an item are read through the source's
// files, as the viewer's views read an attempt's.
import { type ComponentType, createContext, use } from "react";
import { z } from "zod";

import { ApiError, type FileSource, getJson, type Loaded } from "../api/client";
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

/** An item's record.json; download is the file that holds all of it. */
export const itemRecord = z.looseObject({
  id: z.string(),
  revision: z.number(),
  deliverables: z.array(deliverable),
  download: z
    .looseObject({ path: z.string(), size: z.number() })
    .nullable()
    .default(null),
});
export type ItemRecord = z.infer<typeof itemRecord>;

/** An item's ID as the site shows it and searches it: with its revision. */
export const shownId = (item: Listed) => `${item.id}-r${String(item.revision)}`;

/** The URL of an item's page, with its tab when given. */
export function itemHref(p: string, item: string, tab?: string): string {
  const path = `/${encodeURIComponent(p)}/items/${encodeURIComponent(item)}`;
  return tab === undefined
    ? path
    : `${path}?${new URLSearchParams({ tab }).toString()}`;
}

/**
 * Where the site reads from: the URLs of the projects, a project's items
 * and an item's record, an item's files, and how a person signs in and out.
 */
export interface ShareSource {
  projects: string;
  items: (p: string) => string;
  record: (p: string, item: string) => string;
  files: FileSource;
  /** The URL of an item's download, by its path; null for a site with none. */
  download: ((p: string, item: string, path: string) => string) | null;
  access: TokenAccess | HostAccess;
}

/** A person signs in with a token, which the server checks on every read. */
export interface TokenAccess {
  kind: "token";
  /** The page shown before a sign-in, and after a refusal. */
  Landing: ComponentType<{ onSignedIn: () => void }>;
  /** Sign out: null once signed out, else why it failed. */
  signOut: () => Promise<string | null>;
}

/** The host decides who reads the site, before any page loads. */
interface HostAccess {
  kind: "host";
  /** The host's page that signs a person out. */
  logout: string;
}

/** The site's source, which ShareApp gives the pages under it. */
export const ShareSourceContext = createContext<ShareSource | null>(null);

/** The site's source; only a page under ShareApp has one. */
export function useShareSource(): ShareSource {
  const source = use(ShareSourceContext);
  if (source === null) throw new Error("no ShareSource above this page");
  return source;
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
