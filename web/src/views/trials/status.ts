// A trial's status words, the only place they are named, shared with the
// share site's Pass@K view.
import type { Tone, Trial } from "../../api/types";

const SOLVED = "solved";
const NOT_SOLVED = "not solved";
const EXCEPTION = "exception";
const NO_REWARD = "no reward";
export const RUNNING = "running";
const NOT_FINISHED = "not finished";

/**
 * A trial's status: solved when its reward is at least 1.0, as the default
 * `solved` accepts; an exception before anything else. One with no result
 * yet runs while its attempt runs, and else never finished.
 */
export function trialStatus(
  result: Pick<NonNullable<Trial["result"]>, "reward" | "exception"> | null,
  running: boolean,
): { word: string; tone: Tone } {
  if (result === null) {
    return running
      ? { word: RUNNING, tone: "neutral" }
      : { word: NOT_FINISHED, tone: "neutral" };
  }
  const { reward, exception } = result;
  if (exception !== null) return { word: EXCEPTION, tone: "warn" };
  if (reward === null) return { word: NO_REWARD, tone: "neutral" };
  return reward >= 1
    ? { word: SOLVED, tone: "good" }
    : { word: NOT_SOLVED, tone: "bad" };
}
