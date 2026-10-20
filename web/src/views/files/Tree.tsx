// The file tree of a files deliverable: each directory is read when it is
// first opened, directories before the other entries, each group by name.
import { ChevronRight, File, Folder, Link2 } from "lucide-react";
import { type CSSProperties, useId, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { getJson, urls, useJson } from "../../api/client";
import { type Listing, listing } from "../../api/types";

type Entry = Listing["entries"][number];

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

interface TreeProps {
  project: string;
  attempt: string;
  /** The path of the file shown, relative to the attempt. */
  selected: string | null;
}

/** The entries under root, as a tree of links that choose a file. */
export function Tree({ root, ...props }: TreeProps & { root: string }) {
  return (
    <nav aria-label="Files" className="py-2">
      <Entries dir={root} depth={0} {...props} />
    </nav>
  );
}

const indent = (depth: number): CSSProperties => ({
  paddingLeft: `calc(var(--spacing) * ${(3 + depth * 4).toString()})`,
});

function Entries({
  dir,
  depth,
  ...props
}: TreeProps & { dir: string; depth: number }) {
  const loaded = useJson(
    urls.files(props.project, props.attempt, dir),
    listing,
  );
  if (loaded.state !== "ok") {
    return (
      <p style={indent(depth)} className="py-1 pr-3 text-fg-3">
        {loaded.state === "loading" ? "Loading…" : loaded.message}
      </p>
    );
  }
  if (loaded.data.entries.length === 0) {
    return (
      <p style={indent(depth)} className="py-1 pr-3 text-fg-3">
        Empty
      </p>
    );
  }
  return (
    <ul>
      {inTreeOrder(loaded.data.entries).map((entry) => (
        <li key={entry.name}>
          <EntryRow
            entry={entry}
            path={`${dir}/${entry.name}`}
            depth={depth}
            {...props}
          />
        </li>
      ))}
    </ul>
  );
}

const row =
  "target flex w-full items-center gap-2 py-1 pr-3 text-left break-all";

function EntryRow({
  entry,
  path,
  depth,
  ...props
}: TreeProps & { entry: Entry; path: string; depth: number }) {
  const [query] = useSearchParams();
  if (entry.type === "dir") {
    return <DirRow name={entry.name} path={path} depth={depth} {...props} />;
  }
  if (entry.type === "file") {
    const next = new URLSearchParams(query);
    next.set("file", path);
    return (
      <Link
        to={{ search: next.toString() }}
        aria-current={path === props.selected ? "page" : undefined}
        style={indent(depth)}
        className={`${row} text-fg-2 hover:bg-hover hover:text-fg aria-[current=page]:bg-active aria-[current=page]:text-fg`}
      >
        <File className="size-3.5 flex-none text-icon" aria-hidden />
        {entry.name}
      </Link>
    );
  }
  const Icon = entry.type === "symlink" ? Link2 : File;
  return (
    <span style={indent(depth)} className={`${row} text-fg-3`}>
      <Icon className="size-3.5 flex-none text-faint" aria-hidden />
      {entry.name}
      <span className="text-xs">
        {entry.type === "symlink" ? "symlink" : "not a regular file"}
      </span>
    </span>
  );
}

function DirRow({
  name,
  path,
  depth,
  ...props
}: TreeProps & { name: string; path: string; depth: number }) {
  const id = useId();
  const [open, setOpen] = useState(
    props.selected?.startsWith(`${path}/`) ?? false,
  );
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setOpen(!open);
        }}
        style={indent(depth)}
        className={`${row} text-fg-2 hover:bg-hover hover:text-fg`}
      >
        <ChevronRight
          className={`size-3 flex-none text-icon ${open ? "rotate-90" : ""}`}
          aria-hidden
        />
        <Folder className="size-3.5 flex-none text-icon" aria-hidden />
        {name}
      </button>
      <div id={id} hidden={!open}>
        {open && <Entries dir={path} depth={depth + 1} {...props} />}
      </div>
    </>
  );
}
