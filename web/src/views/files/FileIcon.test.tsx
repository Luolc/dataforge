import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FileIcon } from "./FileIcon";

describe("FileIcon", () => {
  it.each([
    "app.ts",
    "App.tsx",
    "main.js",
    "main.go",
    "go.mod",
    "lib.rs",
    "Main.java",
    "util.c",
    "util.cpp",
    "Program.cs",
    "index.php",
    "app.rb",
    "Gemfile",
    "Main.kt",
    "View.swift",
    "Main.scala",
    "main.dart",
    "Lib.hs",
    "init.lua",
    "scan.pl",
    "fit.r",
    "run.jl",
    "app.ex",
    "lib.ml",
    "query.sql",
    "pom.xml",
    "index.html",
    "site.css",
    "table.csv",
    "Cargo.lock",
    ".gitignore",
    "archive.tar.gz",
    "Makefile",
    "CMakeLists.txt",
    "package.json",
    "server.key",
    "build.gradle",
  ])("draws %s with an icon of its own, not the default one", (name) => {
    const { container } = render(<FileIcon name={name} />);
    const svg = container.querySelector("svg");

    expect(svg?.getAttribute("data-kind")).not.toBe("other");
    expect(container.querySelectorAll("[class*='fill-file-']").length).toBe(
      container.querySelectorAll("path, circle").length,
    );
    expect(container.querySelector(".fill-file-other")).toBeNull();
  });
});
