// The time now, for what runs while a page is open.
import { useEffect, useState } from "react";

/** How often a running clock reads the time, in milliseconds. */
const TICK_MS = 1000;

/**
 * The time now in milliseconds since the epoch, read again every TICK_MS
 * while active, and not again once it is not.
 */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      setNow(Date.now());
    }, TICK_MS);
    return () => {
      clearInterval(timer);
    };
  }, [active]);
  return now;
}
