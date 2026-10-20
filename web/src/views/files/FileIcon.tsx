// The icon of each kind of file in the tree, and of a folder.
import {
  Container,
  File,
  FileCode,
  FileCog,
  FileDiff,
  FileTerminal,
  FileText,
  Folder,
  FolderOpen,
  type LucideIcon,
  ScrollText,
  FileType,
} from "lucide-react";

import { type FileKind, fileKindOf } from "./file-kind";

const icons: Record<FileKind, { icon: LucideIcon; colour: string }> = {
  python: { icon: FileCode, colour: "text-syn-function" },
  shell: { icon: FileTerminal, colour: "text-syn-key" },
  config: { icon: FileCog, colour: "text-syn-number" },
  docker: { icon: Container, colour: "text-syn-function" },
  markdown: { icon: FileType, colour: "text-syn-keyword" },
  diff: { icon: FileDiff, colour: "text-syn-keyword" },
  text: { icon: FileText, colour: "text-icon" },
  log: { icon: ScrollText, colour: "text-icon" },
  other: { icon: File, colour: "text-icon" },
};

const size = "size-3.5 flex-none";

/** The icon for the file named name. */
export function FileIcon({ name }: { name: string }) {
  const kind = fileKindOf(name);
  const { icon: Icon, colour } = icons[kind];
  return <Icon className={`${size} ${colour}`} data-kind={kind} aria-hidden />;
}

/** A folder's icon, open or closed. */
export function FolderIcon({ open }: { open: boolean }) {
  const Icon = open ? FolderOpen : Folder;
  return (
    <Icon
      className={`${size} text-icon`}
      data-kind={open ? "folder-open" : "folder"}
      aria-hidden
    />
  );
}
