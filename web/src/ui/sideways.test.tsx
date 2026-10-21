import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Sideways } from "./sideways";

/** Give the box the sizes a browser would lay out, and tell it it scrolled. */
function lay(box: HTMLElement, scrollWidth: number, clientWidth: number) {
  let left = 0;
  Object.defineProperty(box, "scrollWidth", { value: scrollWidth });
  Object.defineProperty(box, "clientWidth", { value: clientWidth });
  Object.defineProperty(box, "scrollLeft", {
    get: () => left,
    set: (value: number) => {
      left = Math.max(0, Math.min(value, scrollWidth - clientWidth));
      box.dispatchEvent(new Event("scroll"));
    },
  });
  act(() => {
    box.dispatchEvent(new Event("scroll"));
  });
}

function show() {
  render(
    <Sideways label="the table">
      <table aria-label="Rows" />
    </Sideways>,
  );
  const box = screen.getByRole("table", { name: "Rows" }).parentElement;
  if (box === null) throw new Error("no box");
  return box;
}

describe("a box that scrolls sideways", () => {
  it("says nothing while what it holds fits", () => {
    lay(show(), 800, 800);

    expect(screen.queryByText(/wider than the page/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says above it that it is wider than the page, and scrolls it from there", () => {
    const box = show();
    lay(box, 2000, 1000);

    expect(
      screen.getByText("The table is wider than the page; scroll it sideways."),
    ).toBeVisible();
    const left = screen.getByRole("button", { name: "Scroll the table left" });
    const right = screen.getByRole("button", {
      name: "Scroll the table right",
    });
    expect(left).toBeDisabled();
    expect(right).toBeEnabled();
    expect(right).toHaveAttribute("aria-controls", box.id);

    fireEvent.click(right);

    expect(box.scrollLeft).toBe(800);
    expect(left).toBeEnabled();
    expect(right).toBeEnabled();

    fireEvent.click(right);

    expect(box.scrollLeft).toBe(1000);
    expect(right).toBeDisabled();

    fireEvent.click(left);

    expect(box.scrollLeft).toBe(200);
    expect(left).toBeEnabled();
  });
});
