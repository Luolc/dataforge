// Reading again, while a page is open, what goes on while an attempt runs.
import { useEffect, useEffectEvent } from "react";

/** How often a page reads again what runs, in milliseconds. */
export const POLL_MS = 5000;

/**
 * Call read every POLL_MS while active and the page is visible: never while
 * the page is hidden, and once at once when it shows again.
 */
export function usePolling(active: boolean, read: () => void): void {
  const onTick = useEffectEvent(read);
  useEffect(() => {
    if (!active) return;
    let timer: ReturnType<typeof setInterval> | undefined;
    const start = () => {
      timer = setInterval(() => {
        onTick();
      }, POLL_MS);
    };
    const stop = () => {
      clearInterval(timer);
      timer = undefined;
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") stop();
      else if (timer === undefined) {
        onTick();
        start();
      }
    };
    if (document.visibilityState !== "hidden") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active]);
}
