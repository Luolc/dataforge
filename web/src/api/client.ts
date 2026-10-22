// Every request the frontend makes to dataforge.viz's API: GETs, and the two
// writes to the job queue.
import { createContext, use, useEffect, useRef, useState } from "react";
import type { z } from "zod";

import { duration } from "../ui/format";
import { POLL_MS } from "./poll";
import { type Listing, listing } from "./types";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ok"; data: T; failed?: Failed };

/** The reads again that failed in a row since data landed, at read. */
export interface Failed {
  message: string;
  times: number;
  /** When the data shown was read, in milliseconds since the epoch. */
  read: number;
}

/**
 * A failed request; body is the response's JSON, null when it had none, and
 * status the status of a response that was not OK, null for any other failure.
 */
export class ApiError extends Error {
  readonly body: unknown;
  readonly status: number | null;

  constructor(
    message: string,
    body: unknown = null,
    status: number | null = null,
  ) {
    super(message);
    this.body = body;
    this.status = status;
  }
}

const api = (p: string) => `/api/projects/${encodeURIComponent(p)}`;

export const urls = {
  projects: () => "/api/projects",
  attempts: (p: string) => `${api(p)}/attempts`,
  record: (p: string, attempt: string) =>
    `${api(p)}/record?${new URLSearchParams({ attempt }).toString()}`,
  live: (p: string, attempt: string) =>
    `${api(p)}/live?${new URLSearchParams({ attempt }).toString()}`,
  files: (p: string, attempt: string, path: string) =>
    `${api(p)}/files?${new URLSearchParams({ attempt, path }).toString()}`,
  tabs: (p: string, attempt: string) =>
    `${api(p)}/tabs?${new URLSearchParams({ attempt }).toString()}`,
  task: (p: string, attempt: string) =>
    `${api(p)}/task?${new URLSearchParams({ attempt }).toString()}`,
  actions: (p: string, item: string) =>
    `${api(p)}/actions?${new URLSearchParams({ item }).toString()}`,
  submit: (
    p: string,
    action: string,
    item: string,
    params: Record<string, string> = {},
  ) =>
    `${api(p)}/actions/${encodeURIComponent(action)}?${new URLSearchParams([["item", item], ...Object.entries(params)]).toString()}`,
  queue: () => "/api/queue",
  cancel: (job: number) => `/api/queue/jobs/${job.toString()}/cancel`,
};

/**
 * Where the views read an attempt's files: a directory's listing, and a
 * file's bytes as the response that carries them, where a path that is no
 * file the source serves, a directory included, is a 404 with its reason;
 * and, when the source has one, the URL a person downloads a file from.
 */
export interface FileSource {
  listing: (
    p: string,
    attempt: string,
    dir: string,
    signal?: AbortSignal,
  ) => Promise<Listing>;
  file: (
    p: string,
    attempt: string,
    path: string,
    signal?: AbortSignal,
  ) => Promise<Response>;
  href?: (p: string, attempt: string, path: string) => string;
}

/** The files of the viewer's and the share server's files route. */
export const apiFiles: FileSource = {
  href: urls.files,
  listing: (p, attempt, dir, signal) =>
    getJson(urls.files(p, attempt, dir), listing, signal),
  file: async (p, attempt, path, signal) => {
    const response = await fetch(
      urls.files(p, attempt, path),
      signal ? { signal } : {},
    );
    // The route answers a directory with its listing, as JSON.
    if (
      response.ok &&
      response.headers.get("content-type")?.startsWith("application/json") ===
        true
    )
      return Response.json(
        { error: "not found", reason: `${path}: a directory, not a file` },
        { status: 404 },
      );
    return response;
  },
};

/** The source the views read files from: the files route, unless given. */
export const FileSourceContext = createContext<FileSource>(apiFiles);

/** The page's source of files. */
export const useFileSource = () => use(FileSourceContext);

/**
 * The header a write carries: a page of another origin cannot send it
 * without the browser asking the server first, which it never allows.
 */
const WRITE_HEADER = { "X-Viz-Write": "1" };

/** GET a URL and parse its JSON with schema; any failure is an ApiError. */
export async function getJson<T>(
  url: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
): Promise<T> {
  return parse(await fetch(url, signal ? { signal } : {}), schema);
}

/** POST to a write's URL, with no body, and parse its JSON with schema. */
export async function postJson<T>(url: string, schema: z.ZodType<T>) {
  return parse(
    await fetch(url, { method: "POST", headers: WRITE_HEADER }),
    schema,
  );
}

async function parse<T>(response: Response, schema: z.ZodType<T>) {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const reason = reasonOf(body) ?? response.statusText;
    throw new ApiError(
      `${response.status.toString()}: ${reason}`,
      body,
      response.status,
    );
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    const where = issue?.path.join(".") ?? "";
    throw new ApiError(
      `unexpected data at ${where !== "" ? where : "the top"}: ${issue?.message ?? ""}`,
    );
  }
  return parsed.data;
}

function reasonOf(body: unknown): string | null {
  if (typeof body === "object" && body !== null && "reason" in body) {
    return typeof body.reason === "string" ? body.reason : null;
  }
  return null;
}

/**
 * The generation of what a page reads while an attempt runs: each read again
 * takes a new one, and what follows the attempt as it goes reads again with
 * it. No two pages, and no two visits of a page, share a generation.
 */
export const RefreshContext = createContext(0);

let generations = 0;

/** A generation no page has had yet. */
export const nextGeneration = () => ++generations;

/** The page's generation of reads, for useLoaded's refresh. */
export const useRefresh = () => use(RefreshContext);

/** How long a read again may take before it counts as failed. */
export const READ_LIMIT_MS = POLL_MS;

/**
 * What load gives, loaded again whenever key changes; a null key loads
 * nothing. The same key must always load the same thing. When refresh
 * changes, the same key loads again, unless a read of it is still out, and
 * what it loaded before stays until the new read lands. A read again that
 * fails keeps it, and says so in failed until a read lands; with limit, so
 * does one with no answer within READ_LIMIT_MS, which is given up. Only a
 * load that ends when its signal aborts may take a limit: one that runs on
 * would be given up on again and again and never land.
 */
export function useLoaded<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  refresh = 0,
  { limit = false }: { limit?: boolean } = {},
): Loaded<T> {
  const [loaded, setLoaded] = useState<{
    key: string;
    value: Loaded<T>;
    read: number;
  }>();
  const outRef = useRef<{
    key: string;
    controller: AbortController;
    timer?: ReturnType<typeof setTimeout>;
  }>(null);
  const again = loaded?.key === key && loaded.value.state === "ok";
  useEffect(() => {
    if (key === null || outRef.current?.key === key) return;
    const controller = new AbortController();
    const read: NonNullable<typeof outRef.current> = { key, controller };
    outRef.current = read;
    const fail = (message: string) => {
      setLoaded((last) => {
        if (last?.key !== key || last.value.state !== "ok")
          return { key, value: { state: "error", message }, read: 0 };
        const times = (last.value.failed?.times ?? 0) + 1;
        return {
          ...last,
          value: {
            state: "ok",
            data: last.value.data,
            failed: { message, times, read: last.read },
          },
        };
      });
    };
    if (limit && again)
      read.timer = setTimeout(() => {
        if (controller.signal.aborted) return;
        controller.abort();
        if (outRef.current === read) outRef.current = null;
        fail(`no answer in ${duration(READ_LIMIT_MS / 1000)}`);
      }, READ_LIMIT_MS);
    load(controller.signal)
      .then(
        (data) => {
          if (controller.signal.aborted) return;
          setLoaded({ key, value: { state: "ok", data }, read: Date.now() });
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          fail(error instanceof Error ? error.message : String(error));
        },
      )
      .finally(() => {
        clearTimeout(read.timer);
        if (outRef.current === read) outRef.current = null;
      });
    // load is a new function on each render, key names what it loads and
    // again only says whether it loads it again.
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [key, refresh]);
  // A read for a key the page has left, or of a page that has gone, is
  // dropped.
  useEffect(
    () => () => {
      clearTimeout(outRef.current?.timer);
      outRef.current?.controller.abort();
      outRef.current = null;
    },
    [key],
  );
  // A result for another key is stale: the new one is still loading.
  return loaded?.key === key ? loaded.value : { state: "loading" };
}

/** The JSON at url, parsed with schema; refresh as for useLoaded. */
export function useJson<T>(
  url: string | null,
  schema: z.ZodType<T>,
  refresh = 0,
) {
  return useLoaded(
    url,
    (signal) => getJson(url ?? "", schema, signal),
    refresh,
  );
}

export interface FileCount {
  files: number;
  complete: boolean;
}

const MAX_DIRS = 200;

/**
 * Count the regular files under a path of an attempt, walking at most
 * MAX_DIRS directories; complete is false when it stopped there.
 */
async function countFiles(
  p: string,
  attempt: string,
  path: string,
  signal?: AbortSignal,
): Promise<FileCount> {
  const queue = [path];
  let files = 0;
  for (let walked = 0; walked < MAX_DIRS; walked++) {
    const dir = queue.shift();
    if (dir === undefined) return { files, complete: true };
    const found = await getJson(urls.files(p, attempt, dir), listing, signal);
    for (const entry of found.entries) {
      if (entry.type === "file") files++;
      if (entry.type === "dir") queue.push(`${dir}/${entry.name}`);
    }
  }
  return { files, complete: queue.length === 0 };
}

// One walk per path and generation of the page's reads, shared by a tab's
// count and a deliverable's card; a walk not yet settled serves the later
// generations too, so walks never pile up. A failed walk is dropped, to be
// tried again.
const fileCounts = new Map<
  string,
  { refresh: number; count: Promise<FileCount>; settled: boolean }
>();

/** countFiles as a hook, for a tab's count and a deliverable's card. */
export function useFileCount(p: string, attempt: string, path: string | null) {
  const refresh = useRefresh();
  const key = path === null ? null : JSON.stringify([p, attempt, path]);
  return useLoaded(
    key,
    () => {
      if (key === null || path === null) return Promise.reject(new Error(""));
      const last = fileCounts.get(key);
      if (last !== undefined && (last.refresh === refresh || !last.settled))
        return last.count;
      const walk = {
        refresh,
        count: countFiles(p, attempt, path),
        settled: false,
      };
      fileCounts.set(key, walk);
      walk.count.then(
        () => {
          walk.settled = true;
        },
        () => {
          walk.settled = true;
          if (fileCounts.get(key) === walk) fileCounts.delete(key);
        },
      );
      return walk.count;
    },
    refresh,
  );
}
