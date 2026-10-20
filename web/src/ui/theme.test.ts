/// <reference types="node" />
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Tests run from web/.
const theme = readFileSync("src/ui/theme.css", "utf8");

const names = (prefix: string) =>
  new Set(
    [...theme.matchAll(new RegExp(`--${prefix}-([a-z0-9-]+):`, "g"))].map(
      (m) => m[1] ?? "",
    ),
  );

describe("the theme's tokens", () => {
  // Tailwind reads text-<name> as a colour when --color-<name> exists, so a
  // type size that shares its name with a colour loses its size.
  it("give no type size the name of a colour", () => {
    const colors = names("color");
    const sizes = [...names("text")].filter((n) => !n.includes("--"));

    expect(sizes.length).toBeGreaterThan(0);
    expect(sizes.filter((n) => colors.has(n))).toEqual([]);
  });
});
