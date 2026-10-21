// The file tree of a files deliverable: each directory is read when it is
// first opened, and again with the page's reads while the attempt runs,
// directories before the other entries, each group by name. A file the
// source only lists shows its name and size, and opens nothing.
import { ChevronRight } from "lucide-react";
import { type CSSProperties, useId, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useFileSource, useLoaded, useRefresh } from "../../api/client";
import { type Entry, inTreeOrder } from "./entries";
import { size } from "./file";
import { FileIcon, FolderIcon, OtherIcon } from "./FileIcon";

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
  const source = useFileSource();
  const loaded = useLoaded(
    JSON.stringify([props.project, props.attempt, dir]),
    (signal) => source.listing(props.project, props.attempt, dir, signal),
    useRefresh(),
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
  if (entry.type === "file" && entry.listed_only === true) {
    return (
      <span style={indent(depth)} className={`${row} text-fg-3`}>
        <FileIcon name={entry.name} />
        {entry.name}
        {entry.size !== null && (
          <span className="ml-auto flex-none text-xs tabular-nums">
            {size(entry.size)}
          </span>
        )}
      </span>
    );
  }
  if (entry.type === "file") {
    const next = new URLSearchParams(query);
    next.set("file", path);
    return (
      <Link
        to={{ search: next.toString() }}
        aria-current={path === props.selected ? "page" : undefined}
        style={indent(depth)}
        className={`${row} text-fg-2 hover:bg-hover hover:text-fg aria-[current=page]:bg-active aria-[current=page]:text-fg aria-[current=page]:shadow-current`}
      >
        <FileIcon name={entry.name} />
        {entry.name}
      </Link>
    );
  }
  return (
    <span style={indent(depth)} className={`${row} text-fg-3`}>
      <OtherIcon />
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
        <FolderIcon open={open} />
        {name}
      </button>
      <div id={id} hidden={!open}>
        {open && <Entries dir={path} depth={depth + 1} {...props} />}
      </div>
    </>
  );
}
