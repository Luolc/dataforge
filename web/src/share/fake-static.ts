// For tests: a fetch that answers as a static host would for the site that
// `dataforge share site` writes from the synthetic export in fixtures/share,
// with index.html for any path it does not hold.
import { vi } from "vitest";
import { z } from "zod";

const ROOT = "../../fixtures/share/";

const files: Record<string, string> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>("../../fixtures/share/**/*", {
      query: "?raw",
      import: "default",
      eager: true,
    }),
  ).map(([key, text]) => [key.slice(ROOT.length), text]),
);

const exported = z
  .looseObject({
    project: z.looseObject({ id: z.string() }),
    session: z.unknown(),
    statuses: z.unknown(),
    columns: z.array(z.unknown()).default([]),
    trials: z.unknown().default(null),
    deliverables: z.array(z.unknown()).default([]),
    kinds: z.unknown().default({}),
    items: z.array(
      z.looseObject({
        id: z.string(),
        revision: z.number(),
        values: z.array(z.unknown()).nullable().default(null),
        reads: z.array(z.unknown()).nullable().default(null),
      }),
    ),
    listed_only: z.array(z.string()).default([]),
  })
  .parse(JSON.parse(files["export.json"] ?? "{}"));

const download = z.looseObject({
  download: z.looseObject({ path: z.string() }).optional(),
});

const bytes = (text: string) => new TextEncoder().encode(text).byteLength;

interface Row {
  name: string;
  type: string;
  size: number | null;
  listed_only?: true;
}

/** An item's tree.json, from the fixture's files under items/<id>/. */
function treeOf(id: string, only: Set<string>): Record<string, Row[]> {
  const dirs = new Map<string, Map<string, Row>>([["", new Map()]]);
  for (const [key, text] of Object.entries(files)) {
    if (!key.startsWith(`items/${id}/`)) continue;
    const parts = key.slice(`items/${id}/`.length).split("/");
    parts.forEach((name, i) => {
      const dir = parts.slice(0, i).join("/");
      const path = parts.slice(0, i + 1).join("/");
      const file = i === parts.length - 1;
      const rows = dirs.get(dir) ?? new Map<string, Row>();
      dirs.set(dir, rows);
      if (!file) dirs.set(path, dirs.get(path) ?? new Map<string, Row>());
      rows.set(name, {
        name,
        type: file ? "file" : "dir",
        size: file ? bytes(text) : null,
        ...(file && only.has(path) ? { listed_only: true } : {}),
      });
    });
  }
  return Object.fromEntries(
    [...dirs].map(([dir, rows]) => [
      dir,
      [...rows.values()].sort((a, b) => (a.name < b.name ? -1 : 1)),
    ]),
  );
}

/**
 * Each path of the site, by URL, and its body; as an older export would
 * give them, with no columns, trials, deliverables or kinds and no item's
 * values, when older.
 */
function site(older: boolean): Map<string, string> {
  const { project, session, statuses, items } = exported;
  const { columns, trials, deliverables, kinds } = exported;
  const base = `/data/${project.id}`;
  const paths = new Map<string, string>([
    [
      "/data/projects.json",
      JSON.stringify([
        older
          ? { ...project, session, statuses }
          : {
              ...project,
              session,
              statuses,
              columns,
              trials,
              deliverables,
              kinds,
            },
      ]),
    ],
    [
      `${base}/items.json`,
      JSON.stringify(
        items.map(({ id, revision, values, reads }) =>
          older ? { id, revision } : { id, revision, values, reads },
        ),
      ),
    ],
  ]);
  for (const { id } of items) {
    const record = files[`items/${id}/record.json`] ?? "{}";
    const zip = download.parse(JSON.parse(record)).download?.path;
    const only = new Set(exported.listed_only);
    if (zip !== undefined) only.add(zip);
    paths.set(`${base}/items/${id}/record.json`, record);
    paths.set(
      `${base}/items/${id}/tree.json`,
      JSON.stringify(treeOf(id, only)),
    );
    for (const [key, text] of Object.entries(files)) {
      const path = key.slice(`items/${id}/`.length);
      if (!key.startsWith(`items/${id}/`)) continue;
      if (path === zip) paths.set(`${base}/items/${id}/${path}`, text);
      else if (!only.has(path))
        paths.set(`${base}/items/${id}/files/${path}`, text);
    }
  }
  return paths;
}

const TYPES: Record<string, string> = {
  json: "application/json",
  md: "text/markdown",
  zip: "application/zip",
};

/** Stub the global fetch with the static host; returns its spy. */
export function fakeStatic({ older = false } = {}) {
  const paths = site(older);
  const fetch = vi.fn((input: string) => {
    const path = decodeURIComponent(new URL(input, "http://static").pathname);
    const body = paths.get(path);
    const type =
      body === undefined
        ? "text/html"
        : (TYPES[path.split(".").at(-1) ?? ""] ?? "application/octet-stream");
    return Promise.resolve(
      new Response(body ?? "<!doctype html>\n", {
        headers: { "Content-Type": type },
      }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
