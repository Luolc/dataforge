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
  typescript: material.Typescript,
  javascript: material.Javascript,
  react: material.React,
  c: material.C,
  cpp: material.Cpp,
  csharp: material.Csharp,
  go: material.Go,
  gomod: material.Gomod,
  rust: material.Rust,
  java: material.Java,
  kotlin: material.Kotlin,
  swift: material.Swift,
  scala: material.Scala,
  dart: material.Dart,
  haskell: material.Haskell,
  lua: material.Lua,
  perl: material.Perl,
  r: material.R,
  julia: material.Julia,
  elixir: material.Elixir,
  ocaml: material.Ocaml,
  php: material.Php,
  ruby: material.Ruby,
  sql: material.Sql,
  xml: material.Xml,
  html: material.Html,
  css: material.Css,
  csv: material.Csv,
  lock: material.Lock,
  git: material.Git,
  archive: material.Archive,
  makefile: material.Makefile,
  cmake: material.Cmake,
  gemfile: material.Gemfile,
  npm: material.Npm,
  key: material.Key,
  gradle: material.Gradle,
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
