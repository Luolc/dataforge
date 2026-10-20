import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useLoaded } from "./client";

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
    expect(hook.result.current).toEqual({ state: "ok", data: "first" });
  });
});
