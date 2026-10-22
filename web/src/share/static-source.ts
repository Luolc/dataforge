// The files of a static site as the site's source, as `dataforge share
// site` writes them: JSON and copies of an export's files under /data/. The
// host decides who reads the site, so there is no token and no sign-in.
import { z } from "zod";

import { type FileSource, getJson } from "../api/client";
import { listing } from "../api/types";
import type { ShareSource } from "./api";

const item = (p: string, id: string) =>
  `/data/${encodeURIComponent(p)}/items/${encodeURIComponent(id)}`;

/** An item's tree.json: each directory's entries, "" for the item's own. */
const tree = z.record(z.string(), listing.shape.entries);
type Tree = z.infer<typeof tree>;

const missing = (path: string) =>
  Response.json(
    { error: "not found", reason: `${path}: not a file this site shows` },
    { status: 404 },
  );

/**
 * An item's files: listings from its tree.json, read once per page load,
 * and a file only when tree.json lists it and not as listed only.
 */
function staticFiles(): FileSource {
  const trees = new Map<string, Promise<Tree>>();
  const treeOf = (p: string, id: string) => {
    const url = `${item(p, id)}/tree.json`;
    let read = trees.get(url);
    if (read === undefined) {
      // Shared by every reader of the item, so no one reader's signal ends it.
      read = getJson(url, tree);
      trees.set(url, read);
      read.catch(() => trees.delete(url));
    }
    return read;
  };
  const href = (p: string, id: string, path: string) =>
    `${item(p, id)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
  return {
    href,
    listing: async (p, id, dir) => {
      const entries = (await treeOf(p, id))[dir];
      if (entries === undefined) throw new Error(`${dir}: no such directory`);
      return { entries };
    },
    file: async (p, id, path, signal) => {
      const slash = path.lastIndexOf("/");
      const dir = slash < 0 ? "" : path.slice(0, slash);
      const name = path.slice(slash + 1);
      const entry = (await treeOf(p, id))[dir]?.find((e) => e.name === name);
      if (entry?.type !== "file" || entry.listed_only === true)
        return missing(path);
      return fetch(href(p, id, path), signal ? { signal } : {});
    },
  };
}

export const staticSource: ShareSource = {
  projects: "/data/projects.json",
  items: (p) => `/data/${encodeURIComponent(p)}/items.json`,
  record: (p, id) => `${item(p, id)}/record.json`,
  files: staticFiles(),
  download: (p, id, path) => `${item(p, id)}/${encodeURIComponent(path)}`,
  access: { kind: "host", logout: "/cdn-cgi/access/logout" },
};
