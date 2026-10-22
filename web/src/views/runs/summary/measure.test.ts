import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canvasMeasure, useMeasure, widest } from "./measure";

/** Tokens of a theme made up for these tests, unlike the app's own. */
const TOKENS = {
  "--font-sans": '"Test Sans"',
  "--font-weight-semibold": "700",
  "--text-md": "20px",
  "--font-weight-normal": "300",
  "--text-xs": "10px",
};

/**
 * A canvas that draws a 0 2 wide, an M 3 and any other character 1, all
 * times its font's size in tens of px and times scale(); it keeps the
 * fonts it was set to.
 */
function fakeCanvas(scale: () => number = () => 1) {
  const fonts: string[] = [];
  const context = {
    font: "",
    measureText(text: string) {
      fonts.push(context.font);
      const size = Number(/(\d+)px/.exec(context.font)?.[1] ?? 0) / 10;
      const each = Array.from(text).map((ch) =>
        ch === "0" ? 2 : ch === "M" ? 3 : 1,
      );
      return { width: each.reduce((a, b) => a + b, 0) * size * scale() };
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
  return fonts;
}

beforeEach(() => {
  for (const [name, value] of Object.entries(TOKENS))
    document.documentElement.style.setProperty(name, value);
});

afterEach(() => {
  vi.unstubAllGlobals();
  for (const name of Object.keys(TOKENS))
    document.documentElement.style.removeProperty(name);
  Reflect.deleteProperty(document, "fonts");
});

describe("a card's text measured", () => {
  it("on a canvas in the page's font, at the theme's weight and size of each line", () => {
    const fonts = fakeCanvas();
    const measure = canvasMeasure();

    expect(measure("MM a", "title")).toBe(16);
    expect(measure("MM a", "sub")).toBe(8);
    expect(fonts).toEqual(['700 20px "Test Sans"', '300 10px "Test Sans"']);
  });

  it("with every digit under a title as a 0, as its tabular figures draw", () => {
    fakeCanvas();
    const measure = canvasMeasure();

    expect(measure("11 runs", "sub")).toBe(measure("00 runs", "sub"));
    expect(measure("11 runs", "sub")).toBe(9);
    expect(measure("11 runs", "title")).toBe(14);
  });

  it("anew once a font finishes loading", () => {
    let scale = 1;
    fakeCanvas(() => scale);
    const fonts = new EventTarget();
    Object.defineProperty(document, "fonts", {
      value: fonts,
      configurable: true,
    });
    const { result } = renderHook(() => useMeasure());
    const before = result.current;
    expect(before("Draft", "title")).toBe(10);

    // The web font draws wider than the one the page drew in before it.
    scale = 2;
    act(() => {
      fonts.dispatchEvent(new Event("loadingdone"));
    });

    expect(result.current).not.toBe(before);
    expect(result.current("Draft", "title")).toBe(20);
  });

  it("as wide as the widest character of its font where there is no canvas, or no token", () => {
    expect(canvasMeasure()).toBe(widest);

    fakeCanvas();
    document.documentElement.style.removeProperty("--text-xs");
    expect(canvasMeasure()).toBe(widest);
    expect(widest("MMMMMMMMMMMMMMM", "title")).toBeGreaterThan(15 * 15);
  });
});
