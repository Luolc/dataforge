// The order of a files deliverable's tree, and the file it opens with.
import { getJson, urls } from "../../api/client";
import { type Listing, listing } from "../../api/types";

export type Entry = Listing["entries"][number];

/** A listing's entries in the tree's order: directories first. */
export function inTreeOrder(entries: Entry[]): Entry[] {
  return [
    ...entries.filter((e) => e.type === "dir"),
    ...entries.filter((e) => e.type !== "dir"),
  ];
}

const MAX_DIRS = 50;

/**
 * The file a tree opens with: instruction.md or README.md at the top, else
 * the first file in the tree's order, reading at most MAX_DIRS directories.
 */
export async function firstFile(
  project: string,
  attempt: string,
  root: string,
  signal?: AbortSignal,
): Promise<string | null> {
  let read = 0;
  async function walk(dir: string): Promise<string | null> {
    if (read++ >= MAX_DIRS) return null;
    const { entries } = await getJson(
      urls.files(project, attempt, dir),
      listing,
      signal,
    );
    if (dir === root) {
      for (const name of ["instruction.md", "README.md"]) {
        if (entries.some((e) => e.type === "file" && e.name === name)) {
          return `${dir}/${name}`;
        }
      }
    }
    for (const entry of inTreeOrder(entries)) {
      const path = `${dir}/${entry.name}`;
      if (entry.type === "file") return path;
      if (entry.type === "dir") {
        const found = await walk(path);
        if (found !== null) return found;
      }
    }
    return null;
  }
  return walk(root);
}
