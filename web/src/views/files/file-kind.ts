// Which kind of file a name is, for the icon the tree shows by it.

export type FileKind =
  | "python"
  | "shell"
  | "config"
  | "docker"
  | "markdown"
  | "diff"
  | "text"
  | "log"
  | "other";

const byName: Record<string, FileKind> = {
  dockerfile: "docker",
  containerfile: "docker",
  makefile: "shell",
};

const byExtension: Record<string, FileKind> = {
  py: "python",
  pyi: "python",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  yaml: "config",
  yml: "config",
  toml: "config",
  json: "config",
  jsonl: "config",
  ini: "config",
  cfg: "config",
  dockerfile: "docker",
  md: "markdown",
  markdown: "markdown",
  patch: "diff",
  diff: "diff",
  txt: "text",
  log: "log",
};

/** The kind of the file named name, matched by whole name, then extension. */
export function fileKindOf(name: string): FileKind {
  const lower = name.toLowerCase();
  const named = byName[lower];
  if (named !== undefined) return named;
  const extension = /\.([^.]+)$/.exec(lower)?.[1];
  return (
    (extension === undefined ? undefined : byExtension[extension]) ?? "other"
  );
}
