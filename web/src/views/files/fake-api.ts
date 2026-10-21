// For tests: a fetch that answers the files API from a map of paths, as
// dataforge.viz would from an attempt's directory.
import { vi } from "vitest";

export type FakeEntry =
  | string
  | Uint8Array<ArrayBuffer>
  | { type: "symlink" }
  | { type: "too-large"; reason: string }
  /** A file the listing marks as listed only, of a size. */
  | { type: "listed"; size: number };

/** Stub the global fetch with the files under each path of files. */
export function fakeFiles(files: Record<string, FakeEntry>) {
  const fetch = vi.fn((input: string) => {
    const path = new URL(input, "http://viz").searchParams.get("path") ?? "";
    return Promise.resolve(answer(files, path));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function answer(files: Record<string, FakeEntry>, path: string): Response {
  const entry = files[path];
  if (typeof entry === "string" || entry instanceof Uint8Array) {
    return new Response(entry);
  }
  if (entry?.type === "too-large") {
    return Response.json(
      { error: "too large", reason: entry.reason },
      { status: 413 },
    );
  }
  const found = new Map<string, string>();
  const listed = new Map<string, number>();
  for (const [key, value] of Object.entries(files)) {
    if (!key.startsWith(`${path}/`)) continue;
    const [name = "", ...rest] = key.slice(path.length + 1).split("/");
    const type =
      rest.length > 0
        ? "dir"
        : typeof value === "object" && "type" in value
          ? value.type === "symlink"
            ? "symlink"
            : "file"
          : "file";
    found.set(name, type);
    if (rest.length === 0 && typeof value === "object" && "type" in value)
      if (value.type === "listed") listed.set(name, value.size);
  }
  if (found.size === 0) {
    return Response.json(
      { error: "not found", reason: `${path} does not exist` },
      { status: 404 },
    );
  }
  const entries = [...found]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([name, type]) => {
      const size = listed.get(name);
      return size === undefined
        ? { name, type, size: type === "file" ? 1 : null }
        : { name, type, size, listed_only: true };
    });
  return Response.json({ entries });
}
