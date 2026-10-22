import { afterEach, describe, expect, it, vi } from "vitest";
import { canvasMeasure, widest } from "./measure";

/**
 * A canvas whose text is as wide as its characters, each 1, a 0 2 and an M
 * 3, times 2 in a 14px font; it keeps the fonts and texts it measured.
 */
function fakeCanvas() {
  const measured: [string, string][] = [];
  const context = {
    font: "",
    measureText(text: string) {
      measured.push([context.font, text]);
      const each = Array.from(text).map((ch) =>
        ch === "0" ? 2 : ch === "M" ? 3 : 1,
      );
      const width = each.reduce((a, b) => a + b, 0);
      return { width: context.font.includes("14px") ? width * 2 : width };
    },
  };
  vi.stubGlobal(
    "OffscreenCanvas",
    class {
      getContext() {
        return context;
      }
    },
  );
  return measured;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.style.removeProperty("--font-sans");
});

describe("a card's text measured", () => {
  it("on a canvas in the page's font, each font with its weight and size", () => {
    document.documentElement.style.setProperty("--font-sans", '"Test Sans"');
    const measured = fakeCanvas();
    const measure = canvasMeasure();

    expect(measure("MM a", "title")).toBe(16);
    expect(measure("MM a", "sub")).toBe(8);
    expect(measured.map(([font]) => font)).toEqual([
      '600 14px "Test Sans"',
      '400 12px "Test Sans"',
    ]);
  });

  it("with every digit under a title as a 0, as its tabular figures draw", () => {
    fakeCanvas();
    const measure = canvasMeasure();

    expect(measure("11 runs", "sub")).toBe(measure("00 runs", "sub"));
    expect(measure("11 runs", "sub")).toBe(9);
    expect(measure("11 runs", "title")).toBe(14);
  });

  it("once for each text", () => {
    const measured = fakeCanvas();
    const measure = canvasMeasure();
    measure("Draft", "title");
    measure("Draft", "title");
    measure("Draft", "sub");

    expect(measured).toHaveLength(2);
  });

  it("as wide as the widest character of its font where there is no canvas", () => {
    const measure = canvasMeasure();

    expect(measure).toBe(widest);
    expect(widest("MMMMMMMMMMMMMMM", "title")).toBeGreaterThan(15 * 15);
  });
});
