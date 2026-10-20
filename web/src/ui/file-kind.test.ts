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
    ["app.ts", "other", "typescript"],
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
    expect(fileTypeOf("archive.tar.gz").kind).toBe("other");
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
