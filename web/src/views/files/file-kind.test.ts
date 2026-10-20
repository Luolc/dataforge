import { describe, expect, it } from "vitest";

import { fileKindOf } from "./file-kind";

describe("fileKindOf", () => {
  it.each([
    ["header.py", "python"],
    ["solve.sh", "shell"],
    ["compose.yaml", "config"],
    ["ci.yml", "config"],
    ["task.toml", "config"],
    ["ports.json", "config"],
    ["Dockerfile", "docker"],
    ["instruction.md", "markdown"],
    ["fix.patch", "diff"],
    ["change.diff", "diff"],
    ["test_ids.txt", "text"],
    ["check.log", "log"],
    ["README.MD", "markdown"],
    ["sample.bin", "other"],
    ["LICENSE", "other"],
    ["archive.tar.gz", "other"],
  ])("takes %s as %s", (name, kind) => {
    expect(fileKindOf(name)).toBe(kind);
  });

  it("matches a whole name before its extension", () => {
    expect(fileKindOf("dockerfile")).toBe("docker");
    expect(fileKindOf("Dockerfile.dev")).toBe("other");
  });
});
