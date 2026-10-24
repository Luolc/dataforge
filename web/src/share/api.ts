// What the share site's pages read, and the source they read it from: the
// share server's API (api-source.ts) or the files of a static site
// (static-source.ts). The files of an item are read through the source's
// files, as the viewer's views read an attempt's.
import { type ComponentType, createContext, use } from "react";
import { z } from "zod";

import { ApiError, type FileSource, getJson, type Loaded } from "../api/client";
import {
  type AttemptRecord,
  harness,
  project,
  type Project,
  READS,
} from "../api/types";

// A column as an export gives it: its values are in each item's row, so it
// names no field to read them from.
const column = project.shape.columns.element.extend({
  field: z.array(z.string()).default([]),
});

/**
 * A project as /api/projects serves it, from its export.json: an older
 * export's has no columns, trials, deliverables or kinds.
 */
const shareProject = project
  .pick({
    id: true,
    name: true,
    color: true,
    session: true,
    statuses: true,
    deliverables: true,
    kinds: true,
    trials: true,
  })
  .extend({ columns: z.array(column).default([]) });
export type ShareProject = z.infer<typeof shareProject>;
export const shareProjects = z.array(shareProject);

/**
 * A project as the viewer's views take one: it has no states, and its items
 * are one level deep.
 */
export const asProject = (p: ShareProject): Project => ({
  ...p,
  states: {},
  item: { depth: 1, title: "ID" },
});

/**
 * An item of a project: its ID, its revision, from 1, and its value in each
 * of the project's columns with how it was read, as the viewer's listing
 * gives an attempt's; none from an older export.
 */
const listed = z.looseObject({
  id: z.string(),
  revision: z.number(),
  values: z.array(z.unknown()).nullable().default(null),
  reads: z.array(z.enum(READS).nullable()).nullable().default(null),
});
export type Listed = z.infer<typeof listed>;
export const listedItems = z.array(listed);

// A trial of an item's trials deliverable, of the record's shape; an older
// export wrote no output, the trial's path standing in for it, and no rewards.
const trial = z
  .looseObject({
    id: z.string().nullable(),
    output: z.string().optional(),
    path: z.string(),
    trace: z
      .looseObject({ path: z.string(), format: z.string().nullable() })
      .nullable(),
    source: z.string().nullable().optional(),
    verdict: z.string().nullable().optional(),
    harness: harness.nullable().optional(),
    result: z
      .looseObject({
        reward: z.number().nullable(),
        rewards: z.record(z.string(), z.number()).default({}),
        exception: z.string().nullable(),
      })
      .nullable(),
  })
  .transform((t) => ({ ...t, output: t.output ?? t.path }));

// A deliverable as an export writes it: of the shape of design.md's "The
// record", the keys that say where in a run it was made null, as they are
// for one made by no run.
const deliverable = z.looseObject({
  name: z.string(),
  kind: z.string(),
  title: z.string(),
  state: z.string().default(""),
  output: z.string().nullable().default(null),
  visit: z.number().nullable().default(null),
  visit_index: z.number().nullable().default(null),
  path: z.string().nullable().default(null),
  reason: z.string().nullable().default(null),
  k: z.number().optional(),
  value: z
    .looseObject({
      output: z.string().nullable().default(null),
      field: z.array(z.string()).default([]),
      value: z.unknown(),
      reason: z.string().nullable().default(null),
    })
    .nullable()
    .optional(),
  verdicts: z
    .looseObject({
      output: z.string().nullable().default(null),
      field: z.array(z.string()).default([]),
      reason: z.string().nullable().default(null),
    })
    .nullable()
    .optional(),
  trials: z.array(trial).optional(),
});

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

/**
 * An item's record as the viewer's views take an attempt's: its
 * deliverables, and no runs.
 */
export const asRecord = (record: ItemRecord): AttemptRecord => ({
  visits: [],
  status: null,
  reason: null,
  kept_visit: null,
  final: {},
  deliverables: record.deliverables,
});

/** An item's ID as the site shows it and searches it: with its revision. */
export const shownId = (item: Pick<Listed, "id" | "revision">) =>
  `${item.id}-r${String(item.revision)}`;

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
