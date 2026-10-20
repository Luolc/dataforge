import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Elapsed, RunningIcon } from "./running";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-03-02T09:45:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Elapsed", () => {
  it("goes on from its start, a second at a time", () => {
    render(
      <p data-testid="t">
        <Elapsed since="2026-03-02T09:43:39+00:00" />
      </p>,
    );
    const shown = () => screen.getByTestId("t").textContent;

    expect(shown()).toBe("1m 21s");
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(shown()).toBe("1m 26s");
  });

  it("adds the seconds before, and shows a start that does not parse as —", () => {
    render(
      <>
        <p data-testid="a">
          <Elapsed since="2026-03-02T09:44:00Z" before={120} />
        </p>
        <p data-testid="b">
          <Elapsed since="not a time" />
        </p>
      </>,
    );

    expect(screen.getByTestId("a").textContent).toBe("3m 0s");
    expect(screen.getByTestId("b").textContent).toBe("—");
  });
});

describe("RunningIcon", () => {
  it("turns, stands still for reduced motion and says Running", () => {
    render(<RunningIcon />);
    const icon = screen.getByRole("img", { name: "Running" });

    expect(icon.querySelector("svg")).toHaveClass(
      "animate-spin",
      "motion-reduce:animate-none",
    );
  });

  it("takes the colour of Running's tone, neutral (L38)", () => {
    render(<RunningIcon />);

    expect(screen.getByRole("img", { name: "Running" })).toHaveClass(
      "text-neutral",
    );
  });
});
