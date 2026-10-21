// Reading an attempt's definition snapshot through the files route, at the
// path the project file's `definition` names.
import { getJson, urls, useLoaded } from "../../api/client";
import { attemptRows, type Project } from "../../api/types";
import { byNewestStart } from "../home/items";
import { fetchFile, size } from "../files/file";
import { type ReadDefinition, readDefinition } from "./types";

/** An attempt's snapshot, or none when its directory holds no such file. */
export type Snapshot = ReadDefinition | { shape: "none" };

/** Read the snapshot of attempt; a failure but a 404 is an error. */
async function readSnapshot(
  project: Project,
  attempt: string,
  signal?: AbortSignal,
): Promise<Snapshot> {
  const path = project.definition ?? "";
  const file = await fetchFile(project.id, attempt, path, signal);
  switch (file.kind) {
    case "missing":
      return { shape: "none" };
    case "too-large":
      throw new Error(file.reason);
    case "binary":
      return {
        shape: "other",
        text: `(${size(file.size)}, not UTF-8)`,
        why: "it is not UTF-8",
      };
    case "text":
      return readDefinition(file.text);
  }
}

/** readSnapshot as a hook; nothing loads for a project with no definition. */
export function useSnapshot(project: Project, attempt: string) {
  const named = project.definition != null;
  return useLoaded(
    named ? JSON.stringify([project.id, attempt, project.definition]) : null,
    (signal) => readSnapshot(project, attempt, signal),
  );
}

/** How many attempts the project's page reads for a snapshot at most. */
export const SCAN_LIMIT = 50;

/** The project's newest snapshot, or how many attempts were read for none. */
export type Newest =
  | { found: true; attempt: string; snapshot: ReadDefinition }
  | { found: false; read: number; all: boolean };

/**
 * Read the snapshots of the project's attempts, newest started first as the
 * home page orders them, empty ones included, one at a time, until one has
 * one or SCAN_LIMIT have been read.
 */
export async function findNewest(
  project: Project,
  signal?: AbortSignal,
): Promise<Newest> {
  const rows = await getJson(urls.attempts(project.id), attemptRows, signal);
  const order = byNewestStart(rows).slice(0, SCAN_LIMIT);
  for (const row of order) {
    // One at a time, newest first, so that the newest one stops the reads.
    const snapshot = await readSnapshot(project, row.id, signal);
    if (snapshot.shape !== "none")
      return { found: true, attempt: row.id, snapshot };
  }
  return {
    found: false,
    read: order.length,
    all: order.length === rows.length,
  };
}
