// A trial's and a test's status words, the only place they are named; a
// trial's are shared with the share site's Pass@K view.
import type { Tone, Trial } from "../../api/types";
import type { CtrfTest } from "./ctrf";

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

/**
 * A test's status words, in the order the Tests tab's filter lists them:
 * CTRF's statuses, and error for a failed test whose rawStatus is error.
 */
export const TEST_WORDS = [
  "passed",
  "failed",
  "error",
  "skipped",
  "pending",
  "other",
] as const;

export type TestWord = (typeof TEST_WORDS)[number];

const TEST_TONES: Record<TestWord, Tone> = {
  passed: "good",
  failed: "bad",
  error: "bad",
  skipped: "neutral",
  pending: "neutral",
  other: "neutral",
};

/** A test status word's tone. */
export function testTone(word: TestWord): Tone {
  return TEST_TONES[word];
}

/** A test's status word and tone, from its CTRF status and rawStatus. */
export function testStatus(test: Pick<CtrfTest, "status" | "rawStatus">): {
  word: TestWord;
  tone: Tone;
} {
  const word =
    test.status === "failed" && test.rawStatus === "error"
      ? "error"
      : test.status;
  return { word, tone: testTone(word) };
}
