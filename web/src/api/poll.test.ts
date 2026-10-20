import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POLL_MS, usePolling } from "./poll";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function setVisibility(state: DocumentVisibilityState) {
  vi.spyOn(document, "visibilityState", "get").mockReturnValue(state);
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

function poll(active: boolean) {
  const read = vi.fn();
  const hook = renderHook(
    ({ on }) => {
      usePolling(on, read);
    },
    { initialProps: { on: active } },
  );
  const advance = (ms: number) => {
    act(() => {
      vi.advanceTimersByTime(ms);
    });
  };
  return { read, advance, rerender: hook.rerender };
}

describe("usePolling", () => {
  it("reads every POLL_MS while active", () => {
    const { read, advance } = poll(true);

    advance(POLL_MS - 1);
    expect(read).not.toHaveBeenCalled();
    advance(1);
    expect(read).toHaveBeenCalledTimes(1);
    advance(POLL_MS * 2);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("reads nothing while not active", () => {
    const { read, advance } = poll(false);

    advance(POLL_MS * 3);
    expect(read).not.toHaveBeenCalled();
  });

  it("stops once it is no longer active", () => {
    const { read, advance, rerender } = poll(true);
    advance(POLL_MS);

    rerender({ on: false });
    advance(POLL_MS * 3);
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("reads nothing while the page is hidden, and at once when it shows again", () => {
    const { read, advance } = poll(true);

    setVisibility("hidden");
    advance(POLL_MS * 3);
    expect(read).not.toHaveBeenCalled();

    setVisibility("visible");
    expect(read).toHaveBeenCalledTimes(1);
    advance(POLL_MS);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("starts only once a page opened hidden shows", () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const { read, advance } = poll(true);

    advance(POLL_MS * 2);
    expect(read).not.toHaveBeenCalled();
    setVisibility("visible");
    expect(read).toHaveBeenCalledTimes(1);
  });
});
