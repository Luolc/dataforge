// What kind of file a name is, and the highlighter's language for it: the one
// table the file tree's icons, the preview and code highlighting all read.

export type FileKind =
  | "python"
  | "shell"
  | "yaml"
  | "toml"
  | "json"
  | "docker"
  | "markdown"
  | "diff"
  | "text"
  | "log"
  | "other";

interface FileType {
  kind: FileKind;
  /** The highlighter's language, or null for plain text. */
  language: string | null;
}

const of = (kind: FileKind, language: string | null): FileType => ({
  kind,
  language,
});
const other = (language: string) => of("other", language);

const byName: Record<string, FileType> = {
  dockerfile: of("docker", null),
};

const byExtension: Record<string, FileType> = {
  py: of("python", "python"),
  pyi: of("python", "python"),
  sh: of("shell", "bash"),
  bash: of("shell", "bash"),
  zsh: of("shell", "bash"),
  yaml: of("yaml", "yaml"),
  yml: of("yaml", "yaml"),
  toml: of("toml", "ini"),
  json: of("json", "json"),
  jsonl: of("json", "json"),
  dockerfile: of("docker", null),
  md: of("markdown", "markdown"),
  markdown: of("markdown", "markdown"),
  diff: of("diff", "diff"),
  patch: of("diff", "diff"),
  txt: of("text", null),
  log: of("log", null),
  js: other("javascript"),
  mjs: other("javascript"),
  ts: other("typescript"),
  tsx: other("typescript"),
  c: other("c"),
  h: other("c"),
  cc: other("cpp"),
  cpp: other("cpp"),
  go: other("go"),
  rs: other("rust"),
  java: other("java"),
  rb: other("ruby"),
  sql: other("sql"),
  xml: other("xml"),
  html: other("xml"),
  css: other("css"),
};

const unknown = of("other", null);

/**
 * The type of the file at path, by its name without regard to case: first the
 * whole name, then the part after its last dot. A name whose only dot is its
 * first character, such as .env, has no extension.
 */
export function fileTypeOf(path: string): FileType {
  const name = (path.split("/").pop() ?? "").toLowerCase();
  const named = byName[name];
  if (named !== undefined) return named;
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return unknown;
  return byExtension[name.slice(dot + 1)] ?? unknown;
}
