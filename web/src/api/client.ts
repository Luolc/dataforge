// Every request the frontend makes: GETs to dataforge.viz's API.
import { createContext, use, useEffect, useState } from "react";
import type { z } from "zod";

import { listing } from "./types";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ok"; data: T };

/** A failed request; body is the response's JSON, null when it had none. */
export class ApiError extends Error {
  readonly body: unknown;

  constructor(message: string, body: unknown = null) {
    super(message);
    this.body = body;
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
};

/** GET a URL and parse its JSON with schema; any failure is an ApiError. */
export async function getJson<T>(
  url: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(url, signal ? { signal } : {});
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const reason = reasonOf(body) ?? response.statusText;
    throw new ApiError(`${response.status.toString()}: ${reason}`, body);
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
 * raises it, and what follows the attempt as it goes reads again with it.
 */
export const RefreshContext = createContext(0);

/** The page's generation of reads, for useLoaded's refresh. */
export const useRefresh = () => use(RefreshContext);

/**
 * What load gives, loaded again whenever key changes; a null key loads
 * nothing. The same key must always load the same thing. When refresh
 * changes, the same key loads again, and what it loaded before stays until
 * the new read lands; a read again that fails keeps it.
 */
export function useLoaded<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  refresh = 0,
): Loaded<T> {
  const [loaded, setLoaded] = useState<{ key: string; value: Loaded<T> }>();
  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        setLoaded({ key, value: { state: "ok", data } });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        setLoaded((last) =>
          last?.key === key && last.value.state === "ok"
            ? last
            : { key, value: { state: "error", message } },
        );
      },
    );
    return () => {
      controller.abort();
    };
    // load is a new function on each render, and key names what it loads.
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [key, refresh]);
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
// count and a deliverable's card; a failed walk is dropped, to be tried again.
const fileCounts = new Map<
  string,
  { refresh: number; count: Promise<FileCount> }
>();

/** countFiles as a hook, for a tab's count and a deliverable's card. */
export function useFileCount(p: string, attempt: string, path: string | null) {
  const refresh = useRefresh();
  const key = path === null ? null : JSON.stringify([p, attempt, path]);
  return useLoaded(
    key,
    () => {
      if (key === null || path === null) return Promise.reject(new Error(""));
      let walk = fileCounts.get(key);
      if (walk?.refresh !== refresh) {
        const made = { refresh, count: countFiles(p, attempt, path) };
        fileCounts.set(key, made);
        made.count.catch(() => {
          if (fileCounts.get(key) === made) fileCounts.delete(key);
        });
        walk = made;
      }
      return walk.count;
    },
    refresh,
  );
}
