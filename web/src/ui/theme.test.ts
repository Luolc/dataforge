/// <reference types="node" />
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Tests run from web/.
const theme = readFileSync("src/ui/theme.css", "utf8");
const main = readFileSync("src/main.tsx", "utf8");

const names = (prefix: string) =>
  new Set(
    [...theme.matchAll(new RegExp(`--${prefix}-([a-z0-9-]+):`, "g"))].map(
      (m) => m[1] ?? "",
    ),
  );

const value = (token: string) =>
  new RegExp(`--${token}:\\s*([^;]+);`).exec(theme)?.[1]?.trim() ?? "";

const color = (name: string) => {
  const hex = value(`color-${name}`);
  expect(hex).toMatch(/^#[0-9a-f]{6}$/);
  return hex;
};

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

const tones = ["good", "bad", "warn", "neutral"];

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

  it.each(
    tones.flatMap((tone) =>
      ["panel", "code-bg", `${tone}-bg`].map((bg) => [tone, bg]),
    ),
  )("give %s text at least 4.5:1 on %s", (tone = "", bg = "") => {
    expect(contrast(color(tone), color(bg))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(tones)("draw the on-tone glyph at least 3:1 on a %s fill", (tone) => {
    expect(contrast(color("on-tone"), color(tone))).toBeGreaterThanOrEqual(3);
  });

  it("ring focus in the accent", () => {
    expect(value("color-focus")).toBe("var(--color-accent)");
  });
});
