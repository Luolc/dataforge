import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { must } from "../../fixtures/sample";
import { Split } from "./split";

const KEY = "test.splitWidth";

// jsdom reads no stylesheet: give the page the tokens theme.css would.
const tokens = {
  "--spacing-side": "256px",
  "--spacing-side-min": "192px",
  "--spacing-side-max": "640px",
  "--spacing-side-step": "16px",
};

beforeEach(() => {
  for (const [name, v] of Object.entries(tokens)) {
    document.documentElement.style.setProperty(name, v);
  }
  Element.prototype.setPointerCapture = () => undefined;
});

afterEach(() => {
  for (const name of Object.keys(tokens)) {
    document.documentElement.style.removeProperty(name);
  }
  localStorage.clear();
  vi.restoreAllMocks();
});

function show() {
  render(
    <Split
      name="side"
      storageKey={KEY}
      label="Resize the side"
      sideClass=""
      side={<nav aria-label="Side">side</nav>}
    >
      <main>main</main>
    </Split>,
  );
  const divider = screen.getByRole("separator", { name: "Resize the side" });
  const grid = must(divider.parentElement);
  return { divider, grid };
}

const width = (divider: HTMLElement) =>
  Number(divider.getAttribute("aria-valuenow"));

describe("a split", () => {
  it("starts at its token's width, a separator that names the column it sizes", () => {
    const { divider, grid } = show();

    expect(width(divider)).toBe(256);
    expect(divider).toHaveAttribute("aria-orientation", "vertical");
    expect(divider).toHaveAttribute("aria-valuemin", "192");
    expect(divider).toHaveAttribute("aria-valuemax", "640");
    expect(divider).toHaveAttribute("tabindex", "0");
    const side = document.getElementById(
      divider.getAttribute("aria-controls") ?? "",
    );
    expect(side).toContainElement(screen.getByRole("navigation"));
    expect(grid.style.gridTemplateColumns).toBe("256px minmax(0,1fr)");
  });

  it("follows a drag of the divider, and keeps the width", () => {
    const { divider, grid } = show();

    fireEvent.pointerDown(divider, { button: 0, clientX: 300 });
    fireEvent.pointerMove(divider, { clientX: 360 });
    expect(width(divider)).toBe(316);
    expect(grid.style.gridTemplateColumns).toBe("316px minmax(0,1fr)");
    fireEvent.pointerMove(divider, { clientX: 260 });
    fireEvent.pointerUp(divider, { clientX: 260 });

    expect(width(divider)).toBe(216);
    expect(localStorage.getItem(KEY)).toBe("216");
    fireEvent.pointerMove(divider, { clientX: 400 });
    expect(width(divider)).toBe(216);
  });

  it("moves a step with an arrow key, and to its least and most with Home and End", () => {
    const { divider } = show();

    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(width(divider)).toBe(272);
    fireEvent.keyDown(divider, { key: "ArrowLeft" });
    fireEvent.keyDown(divider, { key: "ArrowLeft" });
    expect(width(divider)).toBe(240);
    expect(localStorage.getItem(KEY)).toBe("240");
    fireEvent.keyDown(divider, { key: "Home" });
    expect(width(divider)).toBe(192);
    fireEvent.keyDown(divider, { key: "End" });
    expect(width(divider)).toBe(640);
    expect(localStorage.getItem(KEY)).toBe("640");
  });

  it("holds a drag or a key between its least and most", () => {
    const { divider } = show();

    fireEvent.pointerDown(divider, { button: 0, clientX: 300 });
    fireEvent.pointerMove(divider, { clientX: 0 });
    expect(width(divider)).toBe(192);
    fireEvent.pointerMove(divider, { clientX: 2000 });
    fireEvent.pointerUp(divider, { clientX: 2000 });
    expect(width(divider)).toBe(640);
    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(width(divider)).toBe(640);
    fireEvent.keyDown(divider, { key: "Home" });
    fireEvent.keyDown(divider, { key: "ArrowLeft" });
    expect(width(divider)).toBe(192);
  });

  it("is no wider than half the split", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      new DOMRect(0, 0, 900, 400),
    );
    const { divider } = show();

    expect(divider).toHaveAttribute("aria-valuemax", "450");
    fireEvent.keyDown(divider, { key: "End" });
    expect(width(divider)).toBe(450);
  });

  it("starts at the width kept, held to its bounds", () => {
    localStorage.setItem(KEY, "300");
    expect(width(show().divider)).toBe(300);
  });

  it.each([
    ["5", 192],
    ["99999", 640],
  ])("holds a kept width of %s to %i", (kept, shown) => {
    localStorage.setItem(KEY, kept);
    expect(width(show().divider)).toBe(shown);
  });

  it.each(["wide", "", "-40", "12.5px"])(
    "starts at its token's width when what is kept, %j, is no width",
    (kept) => {
      localStorage.setItem(KEY, kept);
      expect(width(show().divider)).toBe(256);
    },
  );
});
