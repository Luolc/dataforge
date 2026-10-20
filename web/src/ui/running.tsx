// What a page shows of something that runs now: a turning icon and a time
// that goes on.
import { LoaderCircle } from "lucide-react";

import { duration } from "./format";
import { useNow } from "./now";
import { liveStatus, toneText } from "./status";

const RUNNING = liveStatus.running;

/**
 * Running as an icon that turns, and stands still when the reader asks for
 * reduced motion; its label is read out, not shown.
 */
export function RunningIcon() {
  return (
    <span
      role="img"
      aria-label={RUNNING.label}
      title={RUNNING.label}
      className={`inline-grid size-icon-md flex-none place-items-center ${toneText[RUNNING.tone]}`}
    >
      <LoaderCircle
        className="size-icon-md animate-spin motion-reduce:animate-none"
        aria-hidden
      />
    </span>
  );
}

/**
 * The time from since to now, read again every second, plus the seconds
 * before; "—" when since does not parse.
 */
export function Elapsed({
  since,
  before = 0,
}: {
  since: string;
  before?: number;
}) {
  const now = useNow(true);
  const from = Date.parse(since);
  if (Number.isNaN(from)) return "—";
  return duration(before + (now - from) / 1000);
}
