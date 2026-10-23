/// <reference types="node" />
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { TEMPLATES } from "./template";

// Tests run from web/.
const theme = readFileSync("src/ui/theme.css", "utf8");
const main = readFileSync("src/main.tsx", "utf8");
const fileIconsSource = readFileSync(
  "src/views/files/icons/material.tsx",
  "utf8",
);

const names = (prefix: string) =>
  new Set(
    [...theme.matchAll(new RegExp(`--${prefix}-([a-z0-9-]+):`, "g"))].map(
      (m) => m[1] ?? "",
    ),
  );

const value = (token: string) =>
  new RegExp(`--${token}:\\s*([^;]+);`).exec(theme)?.[1]?.trim() ?? "";

// WCAG 2 contrast ratio of two opaque colours.
const contrast = (a: string, b: string) => {
  const luminance = (hex: string) => {
    const [r = 0, g = 0, b = 0] = [1, 3, 5].map((i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
};

const tones = ["good", "bad", "warn", "neutral"] as const;

// Each template's colour tokens: the default's in @theme, every other's in
// its own html[data-template] block.
const colours = (block: string) =>
  new Map(
    [...block.matchAll(/--color-([a-z0-9-]+):\s*([^;]+);/g)].map((m) => [
      m[1] ?? "",
      (m[2] ?? "").trim(),
    ]),
  );
const themeBlock = /@theme \{([\s\S]*?)\n\}/.exec(theme)?.[1] ?? "";
const templateIds: string[] = TEMPLATES.map((t) => t.id);
const blocks = new Map(
  templateIds.map((id) => [
    id,
    id === "contrast"
      ? colours(themeBlock)
      : colours(
          new RegExp(`html\\[data-template="${id}"\\] \\{([^}]*)\\}`).exec(
            theme,
          )?.[1] ?? "",
        ),
  ]),
);

// A token's colour in a template, following var(--color-...) within it.
const colour = (id: string, name: string): string => {
  const v = blocks.get(id)?.get(name) ?? "";
  const ref = /^var\(--color-([a-z0-9-]+)\)$/.exec(v);
  if (ref) return colour(id, ref[1] ?? "");
  expect(`${id}: --color-${name}: ${v}`).toMatch(/: #[0-9a-f]{6}$/);
  return v;
};

// [template, text token, surface token, the least ratio]
// The colours of the file tree's icons, views/files/icons/material.tsx.
const fileIcons = [
  "python",
  "python-2",
  "shell",
  "yaml",
  "toml",
  "toml-2",
  "json",
  "docker",
  "markdown",
  "diff",
  "text",
  "log",
  "other",
  "folder",
  "typescript",
  "javascript",
  "react",
  "c",
  "cpp",
  "csharp",
  "go",
  "gomod",
  "rust",
  "java",
  "kotlin",
  "swift",
  "scala",
  "dart",
  "dart-2",
  "dart-3",
  "haskell",
  "haskell-2",
  "haskell-3",
  "lua",
  "perl",
  "r",
  "julia",
  "julia-2",
  "julia-3",
  "elixir",
  "ocaml",
  "php",
  "ruby",
  "sql",
  "xml",
  "html",
  "css",
  "csv",
  "lock",
  "git",
  "archive",
  "makefile",
  "cmake",
  "cmake-2",
  "cmake-3",
  "gemfile",
  "npm",
  "key",
  "gradle",
].map((name) => `file-${name}`);

const pairs = (
  texts: string[],
  surfaces: string[],
  least: number,
): [string, string, string, number][] =>
  templateIds.flatMap((id) =>
    texts.flatMap((t) =>
      surfaces.map((bg): [string, string, string, number] => [
        id,
        t,
        bg,
        least,
      ]),
    ),
  );

const surfaces = ["panel", "card", "head", "hover", "active"];

describe("the theme's tokens", () => {
  // Tailwind reads text-<name> as a colour when --color-<name> exists, so a
  // type size that shares its name with a colour loses its size.
  it("give no type size the name of a colour", () => {
    const colors = names("color");
    const sizes = [...names("text")].filter((n) => !n.includes("--"));

    expect(sizes.length).toBeGreaterThan(0);
    expect(sizes.filter((n) => colors.has(n))).toEqual([]);
  });

  it("set a light colour scheme", () => {
    expect(theme).toMatch(/color-scheme:\s*light;/);
  });

  it("set text in Mona Sans and code in Fira Code, both bundled", () => {
    expect(value("font-sans")).toMatch(/^"Mona Sans Variable",/);
    expect(value("font-mono")).toMatch(/^"Fira Code Variable",/);
    expect(main).toContain('import "@fontsource-variable/mona-sans";');
    expect(main).toContain('import "@fontsource-variable/fira-code";');
  });

  it("leave ligatures on", () => {
    expect(theme).not.toMatch(/font-variant-ligatures/);
  });

  it("check the contrast of every colour the file tree's icons draw", () => {
    const drawn = new Set(
      [...fileIconsSource.matchAll(/fill-(file-[a-z0-9-]+)/g)].map((m) => m[1]),
    );
    expect([...drawn].sort()).toEqual([...fileIcons].sort());
  });

  it("draw the dark template in a dark colour scheme", () => {
    expect(theme).toMatch(
      /html\[data-template="dark"\] \{\s*color-scheme: dark;/,
    );
  });

  it.each(templateIds)("%s: list each colour token the default has", (id) => {
    const names = [...(blocks.get("contrast")?.keys() ?? [])];
    expect(names.length).toBeGreaterThan(40);
    expect([...(blocks.get(id)?.keys() ?? [])]).toEqual(names);
  });

  it.each([
    ...pairs([...tones], [...surfaces, "neutral-bg"], 7),
    ...tones.flatMap((tone) =>
      templateIds.map((id): [string, string, string, number] => [
        id,
        tone,
        `${tone}-bg`,
        7,
      ]),
    ),
    ...tones.flatMap((tone) =>
      templateIds.map((id): [string, string, string, number] => [
        id,
        `${tone}-label-fg`,
        `${tone}-label`,
        7,
      ]),
    ),
    ...pairs(
      ["fg"],
      [...surfaces, "app", "group", "tree", "code-bg", "chip"],
      7,
    ),
    ...pairs(["fg-2"], [...surfaces, "group", "tree", "chip"], 7),
    ...pairs(["fg-3"], ["panel", "card", "head", "tree", "chip"], 7),
    ...pairs(["chip-on-fg", "chip-on-count"], ["chip-on"], 7),
    ...pairs(["accent"], ["panel", "hover", "active"], 4.5),
    ...pairs(
      ["faint"],
      [...surfaces, "app", "group", "tree", "code-bg", "code-gutter"],
      4.5,
    ),
    ...pairs(
      [
        "syn-comment",
        "syn-string",
        "syn-keyword",
        "syn-number",
        "syn-function",
        "syn-variable",
        "syn-key",
      ],
      ["code-bg"],
      4.5,
    ),
    ...pairs(["chip-line", "icon"], ["panel", "card", "tree"], 3),
    ...pairs([...tones], ["tree", "group"], 3),
    ...pairs(fileIcons, ["tree", "hover", "active"], 3),
    ...pairs(
      ["series-1", "series-2", "series-3", "bar", "accent", "warn"],
      ["card", "warn-bg"],
      3,
    ),
  ])("%s: %s on %s at least %d:1", (id, text, bg, least) => {
    expect(contrast(colour(id, text), colour(id, bg))).toBeGreaterThanOrEqual(
      least,
    );
  });

  it.each(templateIds)(
    "%s: give the surfaces their steps, with code apart from the card",
    (id) => {
      expect(colour(id, "code-bg")).not.toBe(colour(id, "card"));
      expect(colour(id, "head")).not.toBe(colour(id, "card"));
      expect(colour(id, "group")).not.toBe(colour(id, "panel"));
    },
  );

  it("give a link the accent through its utility", () => {
    expect(theme).toMatch(/@utility link \{\s*color: var\(--color-accent\);/);
  });

  it("draw every Lucide icon at the one stroke width", () => {
    expect(theme).toMatch(
      /svg\.lucide \{\s*stroke-width: var\(--icon-stroke\);/,
    );
    expect(theme).toMatch(/--icon-stroke: 1\.75;/);
  });

  it("ring focus in the accent", () => {
    expect(value("color-focus")).toBe("var(--color-accent)");
  });
});

describe("rendered Markdown", () => {
  const rule = (selector: string) =>
    new RegExp(`\\${selector} \\{([^}]*)\\}`).exec(theme)?.[1] ?? "";

  it("breaks a word longer than its line, and scrolls a wide table on its own", () => {
    expect(rule(".md")).toMatch(/overflow-wrap: break-word;/);
    expect(rule(".md table")).toMatch(/display: block;/);
    expect(rule(".md table")).toMatch(/max-width: 100%;/);
    expect(rule(".md table")).toMatch(/overflow-x: auto;/);
    expect(rule(".md a")).toMatch(/max-width: 100%;/);
    expect(rule(".md :where\\(th, td\\) code")).toMatch(
      /overflow-wrap: anywhere;/,
    );
  });
});
