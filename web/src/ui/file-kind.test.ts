import { describe, expect, it } from "vitest";

import { fileTypeOf } from "./file-kind";
import { languageOf } from "./language";

describe("fileTypeOf", () => {
  it.each([
    ["header.py", "python", "python"],
    ["solve.sh", "shell", "bash"],
    ["compose.yaml", "yaml", "yaml"],
    ["ci.yml", "yaml", "yaml"],
    ["task.toml", "toml", "ini"],
    ["ports.json", "json", "json"],
    ["Dockerfile", "docker", null],
    ["instruction.md", "markdown", "markdown"],
    ["notes.markdown", "markdown", "markdown"],
    ["fix.patch", "diff", "diff"],
    ["change.diff", "diff", "diff"],
    ["test_ids.txt", "text", null],
    ["check.log", "log", null],
    ["app.ts", "typescript", "typescript"],
    ["App.tsx", "react", "typescript"],
    ["index.mjs", "javascript", "javascript"],
    ["main.go", "go", "go"],
    ["go.mod", "gomod", null],
    ["lib.rs", "rust", "rust"],
    ["Main.java", "java", "java"],
    ["util.cc", "cpp", "cpp"],
    ["util.h", "c", "c"],
    ["Program.cs", "csharp", "csharp"],
    ["index.php", "php", "php"],
    ["bug12345.phpt", "php", "php"],
    ["app.rb", "ruby", "ruby"],
    ["Gemfile", "gemfile", "ruby"],
    ["Main.kt", "kotlin", "kotlin"],
    ["View.swift", "swift", "swift"],
    ["init.lua", "lua", "lua"],
    ["scan.pl", "perl", "perl"],
    ["fit.R", "r", "r"],
    ["Main.scala", "scala", null],
    ["main.dart", "dart", null],
    ["Lib.hs", "haskell", null],
    ["run.jl", "julia", null],
    ["app.ex", "elixir", null],
    ["lib.ml", "ocaml", null],
    ["query.sql", "sql", "sql"],
    ["pom.xml", "xml", "xml"],
    ["index.html", "html", "xml"],
    ["site.css", "css", "css"],
    ["table.csv", "csv", null],
    ["Makefile", "makefile", "makefile"],
    ["rules.mk", "makefile", "makefile"],
    ["CMakeLists.txt", "cmake", null],
    ["package.json", "npm", "json"],
    ["package-lock.json", "lock", "json"],
    ["Cargo.lock", "lock", null],
    ["go.sum", "lock", null],
    ["Cargo.toml", "toml", "ini"],
    ["build.gradle", "gradle", null],
    ["build.gradle.kts", "gradle", "kotlin"],
    [".gitignore", "git", null],
    ["archive.tar.gz", "archive", null],
    ["tool.pyz", "archive", null],
    ["server.key", "key", null],
    ["run.out", "log", null],
    ["run.err", "log", null],
    ["sample.bin", "other", null],
  ])("takes %s as %s, highlighted as %s", (name, kind, language) => {
    expect(fileTypeOf(name)).toEqual({ kind, language });
    expect(languageOf(name)).toBe(language);
  });

  it("ignores case", () => {
    expect(fileTypeOf("Main.PY").kind).toBe("python");
    expect(fileTypeOf("DOCKERFILE").kind).toBe("docker");
  });

  it("matches a whole name before its extension, and the last dot only", () => {
    expect(fileTypeOf("Dockerfile.dev").kind).toBe("other");
    expect(fileTypeOf("api.dockerfile").kind).toBe("docker");
    expect(fileTypeOf("archive.tar.bin").kind).toBe("other");
    expect(fileTypeOf("report.md.txt").kind).toBe("text");
  });

  it("gives a name whose only dot comes first no extension", () => {
    expect(fileTypeOf(".md").kind).toBe("other");
    expect(fileTypeOf(".env").kind).toBe("other");
  });

  it("reads only the last part of a path", () => {
    expect(fileTypeOf("src/tinyparse.py/README").kind).toBe("other");
    expect(fileTypeOf("environment/Dockerfile").kind).toBe("docker");
  });
});
