// The icon of each kind of file in the tree, and of a folder.
import type { ComponentType, SVGProps } from "react";

import { type FileKind, fileTypeOf } from "../../ui/file-kind";
import * as material from "./icons/material";

const icons: Record<FileKind, ComponentType<SVGProps<SVGSVGElement>>> = {
  python: material.Python,
  shell: material.Shell,
  yaml: material.Yaml,
  toml: material.Toml,
  json: material.Json,
  docker: material.Docker,
  markdown: material.Markdown,
  diff: material.Diff,
  text: material.Text,
  log: material.Log,
  other: material.Other,
};

const size = "size-4 flex-none";

/** The icon for the file named name. */
export function FileIcon({ name }: { name: string }) {
  const { kind } = fileTypeOf(name);
  const Icon = icons[kind];
  return <Icon className={size} data-kind={kind} />;
}

/** The icon of an entry that is not a regular file, such as a symlink. */
export function OtherIcon() {
  return <material.Other className={size} data-kind="other" />;
}

/** A folder's icon, open or closed. */
export function FolderIcon({ open }: { open: boolean }) {
  const Icon = open ? material.FolderOpen : material.Folder;
  return <Icon className={size} data-kind={open ? "folder-open" : "folder"} />;
}
