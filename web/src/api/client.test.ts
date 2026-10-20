import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { READ_LIMIT_MS, useLoaded } from "./client";

afterEach(() => {
  vi.useRealTimers();
});

/** A promise and the functions that settle it. */
function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: Error) => void = () => undefined;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe("useLoaded read again", () => {
  function load() {
    const reads = [deferred<string>(), deferred<string>(), deferred<string>()];
    let n = 0;
    const hook = renderHook(
      ({ refresh }) =>
        useLoaded(
          "key",
          () => (reads[n++] ?? deferred<string>()).promise,
          refresh,
        ),
      { initialProps: { refresh: 0 } },
    );
    return { reads, hook };
  }

  it("keeps what it loaded until the new read lands", async () => {
    const { reads, hook } = load();
    await act(async () => {
      reads[0]?.resolve("first");
      await reads[0]?.promise;
    });
    expect(hook.result.current).toEqual({ state: "ok", data: "first" });

    hook.rerender({ refresh: 1 });
    expect(hook.result.current).toEqual({ state: "ok", data: "first" });

    await act(async () => {
      reads[1]?.resolve("second");
      await reads[1]?.promise;
    });
    expect(hook.result.current).toEqual({ state: "ok", data: "second" });
  });

  it("keeps what it loaded when a read again fails", async () => {
    const { reads, hook } = load();
    await act(async () => {
      reads[0]?.resolve("first");
      await reads[0]?.promise;
    });

    hook.rerender({ refresh: 1 });
    await act(async () => {
      reads[1]?.reject(new Error("500: half written"));
      // The hook's handler was attached first, so it has run by now.
      await reads[1]?.promise.catch(() => undefined);
    });
    expect(hook.result.current).toMatchObject({
      state: "ok",
      data: "first",
      failed: { message: "500: half written", times: 1 },
    });
  });

  it("counts the reads again that fail in a row, until one lands", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const reads = [
      deferred<string>(),
      deferred<string>(),
      deferred<string>(),
      deferred<string>(),
    ];
    let n = 0;
    const hook = renderHook(
      ({ refresh }) =>
        useLoaded("key", () => must(reads[n++]).promise, refresh),
      { initialProps: { refresh: 0 } },
    );
    const settle = async (i: number, value: string | Error) => {
      await act(async () => {
        const read = must(reads[i]);
        if (value instanceof Error) read.reject(value);
        else read.resolve(value);
        await read.promise.catch(() => undefined);
      });
    };
    vi.setSystemTime(new Date("2026-03-02T09:00:00Z"));
    await settle(0, "first");
    expect(hook.result.current).toEqual({ state: "ok", data: "first" });

    vi.setSystemTime(new Date("2026-03-02T09:05:00Z"));
    hook.rerender({ refresh: 1 });
    await settle(1, new Error("502: gone"));
    hook.rerender({ refresh: 2 });
    await settle(2, new Error("502: gone again"));
    // When the data shown was read: before either failure.
    expect(hook.result.current).toEqual({
      state: "ok",
      data: "first",
      failed: {
        message: "502: gone again",
        times: 2,
        read: Date.parse("2026-03-02T09:00:00Z"),
      },
    });

    hook.rerender({ refresh: 3 });
    await settle(3, "second");
    expect(hook.result.current).toEqual({ state: "ok", data: "second" });
  });
});

function must<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("missing");
  return value;
}

describe("useLoaded read again, slow", () => {
  function load() {
    const reads: ReturnType<typeof deferred<string>>[] = [];
    const hook = renderHook(
      ({ refresh }) =>
        useLoaded(
          "key",
          () => {
            const read = deferred<string>();
            reads.push(read);
            return read.promise;
          },
          refresh,
        ),
      { initialProps: { refresh: 0 } },
    );
    return { reads, hook };
  }

  const settle = async (
    read: ReturnType<typeof deferred<string>> | undefined,
    value: string,
  ) => {
    await act(async () => {
      must(read).resolve(value);
      await must(read).promise;
    });
  };

  it("sends no other while one is out, and lands a slow one", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { reads, hook } = load();
    await settle(reads[0], "first");

    hook.rerender({ refresh: 1 });
    hook.rerender({ refresh: 2 });
    hook.rerender({ refresh: 3 });
    expect(reads).toHaveLength(2);

    act(() => {
      vi.advanceTimersByTime(READ_LIMIT_MS - 1);
    });
    await settle(reads[1], "second");
    expect(hook.result.current).toEqual({ state: "ok", data: "second" });
  });

  it("counts a read again with no answer within READ_LIMIT_MS as failed", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { reads, hook } = load();
    await settle(reads[0], "first");

    hook.rerender({ refresh: 1 });
    act(() => {
      vi.advanceTimersByTime(READ_LIMIT_MS);
    });
    expect(hook.result.current).toMatchObject({
      state: "ok",
      data: "first",
      failed: { message: "no answer in 5s", times: 1 },
    });
    // The read given up on lands nothing; the next one is sent.
    await settle(reads[1], "late");
    expect(hook.result.current).toMatchObject({ data: "first" });
    hook.rerender({ refresh: 2 });
    expect(reads).toHaveLength(3);
    await settle(reads[2], "second");
    expect(hook.result.current).toEqual({ state: "ok", data: "second" });
  });
});
