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
  | "typescript"
  | "javascript"
  | "react"
  | "c"
  | "cpp"
  | "csharp"
  | "go"
  | "gomod"
  | "rust"
  | "java"
  | "kotlin"
  | "swift"
  | "scala"
  | "dart"
  | "haskell"
  | "lua"
  | "perl"
  | "r"
  | "julia"
  | "elixir"
  | "ocaml"
  | "php"
  | "ruby"
  | "sql"
  | "xml"
  | "html"
  | "css"
  | "csv"
  | "lock"
  | "git"
  | "archive"
  | "makefile"
  | "cmake"
  | "gemfile"
  | "npm"
  | "key"
  | "gradle"
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

const byName: Record<string, FileType> = {
  dockerfile: of("docker", null),
  makefile: of("makefile", "makefile"),
  gnumakefile: of("makefile", "makefile"),
  "cmakelists.txt": of("cmake", null),
  gemfile: of("gemfile", "ruby"),
  rakefile: of("ruby", "ruby"),
  "package.json": of("npm", "json"),
  "go.mod": of("gomod", null),
  "go.work": of("gomod", null),
  "go.sum": of("lock", null),
  "package-lock.json": of("lock", "json"),
  "pnpm-lock.yaml": of("lock", "yaml"),
  "build.gradle.kts": of("gradle", "kotlin"),
  "settings.gradle.kts": of("gradle", "kotlin"),
  ".gitignore": of("git", null),
  ".gitattributes": of("git", null),
  ".gitmodules": of("git", "ini"),
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
  out: of("log", null),
  err: of("log", null),
  ts: of("typescript", "typescript"),
  mts: of("typescript", "typescript"),
  cts: of("typescript", "typescript"),
  js: of("javascript", "javascript"),
  mjs: of("javascript", "javascript"),
  cjs: of("javascript", "javascript"),
  tsx: of("react", "typescript"),
  jsx: of("react", "javascript"),
  c: of("c", "c"),
  h: of("c", "c"),
  cc: of("cpp", "cpp"),
  cpp: of("cpp", "cpp"),
  cxx: of("cpp", "cpp"),
  hh: of("cpp", "cpp"),
  hpp: of("cpp", "cpp"),
  hxx: of("cpp", "cpp"),
  cs: of("csharp", "csharp"),
  go: of("go", "go"),
  rs: of("rust", "rust"),
  java: of("java", "java"),
  kt: of("kotlin", "kotlin"),
  kts: of("kotlin", "kotlin"),
  swift: of("swift", "swift"),
  scala: of("scala", null),
  sc: of("scala", null),
  dart: of("dart", null),
  hs: of("haskell", null),
  lua: of("lua", "lua"),
  pl: of("perl", "perl"),
  pm: of("perl", "perl"),
  r: of("r", "r"),
  jl: of("julia", null),
  ex: of("elixir", null),
  exs: of("elixir", null),
  ml: of("ocaml", null),
  mli: of("ocaml", null),
  php: of("php", "php"),
  phpt: of("php", "php"),
  phtml: of("php", "php"),
  rb: of("ruby", "ruby"),
  rake: of("ruby", "ruby"),
  gemspec: of("ruby", "ruby"),
  sql: of("sql", "sql"),
  xml: of("xml", "xml"),
  html: of("html", "xml"),
  htm: of("html", "xml"),
  css: of("css", "css"),
  scss: of("css", "scss"),
  less: of("css", "less"),
  csv: of("csv", null),
  tsv: of("csv", null),
  lock: of("lock", null),
  gz: of("archive", null),
  tgz: of("archive", null),
  zip: of("archive", null),
  tar: of("archive", null),
  bz2: of("archive", null),
  xz: of("archive", null),
  zst: of("archive", null),
  pyz: of("archive", null),
  jar: of("archive", null),
  whl: of("archive", null),
  mk: of("makefile", "makefile"),
  cmake: of("cmake", null),
  gradle: of("gradle", null),
  key: of("key", null),
  pem: of("key", null),
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
