// Every request the frontend makes: GETs to dataforge.viz's API.
import { useEffect, useState } from "react";
import type { z } from "zod";

import { listing } from "./types";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ok"; data: T };

export class ApiError extends Error {}

const api = (p: string) => `/api/projects/${encodeURIComponent(p)}`;

export const urls = {
  projects: () => "/api/projects",
  attempts: (p: string) => `${api(p)}/attempts`,
  record: (p: string, attempt: string) =>
    `${api(p)}/record?${new URLSearchParams({ attempt }).toString()}`,
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
    throw new ApiError(`${response.status.toString()}: ${reason}`);
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
 * What load gives, loaded again whenever key changes; a null key loads
 * nothing. The same key must always load the same thing.
 */
export function useLoaded<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
): Loaded<T> {
  const [loaded, setLoaded] = useState<{ key: string; value: Loaded<T> }>();
  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => {
        setLoaded({ key, value: { state: "ok", data } });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        setLoaded({ key, value: { state: "error", message } });
      },
    );
    return () => {
      controller.abort();
    };
    // load is a new function on each render, and key names what it loads.
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [key]);
  // A result for another key is stale: the new one is still loading.
  return loaded?.key === key ? loaded.value : { state: "loading" };
}

/** The JSON at url, parsed with schema. */
export function useJson<T>(url: string | null, schema: z.ZodType<T>) {
  return useLoaded(url, (signal) => getJson(url ?? "", schema, signal));
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

// One walk per path while the page is open, shared by a tab's count and a
// deliverable's card; a failed walk is dropped, to be tried again. The page
// shows what was on disk when it was loaded, as everywhere else.
const fileCounts = new Map<string, Promise<FileCount>>();

/** countFiles as a hook, for a tab's count and a deliverable's card. */
export function useFileCount(p: string, attempt: string, path: string | null) {
  const key = path === null ? null : JSON.stringify([p, attempt, path]);
  return useLoaded(key, () => {
    if (key === null || path === null) return Promise.reject(new Error(""));
    let count = fileCounts.get(key);
    if (count === undefined) {
      count = countFiles(p, attempt, path);
      fileCounts.set(key, count);
      count.catch(() => fileCounts.delete(key));
    }
    return count;
  });
}
