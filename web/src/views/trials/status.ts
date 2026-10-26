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
 * A trial's status: its verdict, the dataset's word for it, as the project
 * names it, when it has one, with exception marking that it also ended on
 * one; else when reward is known, solved if reward >= 1.0, not solved if <
 * 1.0, with exception as a marker; else if no reward but exception exists,
 * the exception; else no reward. One with no result yet runs while its
 * attempt runs, and else never finished.
 */
export function trialStatus(
  result: Pick<NonNullable<Trial["result"]>, "reward" | "exception"> | null,
  running: boolean,
  verdict: { label: string; tone: Tone } | null = null,
): { word: string; tone: Tone; exception: boolean } {
  if (result === null) {
    return running
      ? { word: RUNNING, tone: "neutral", exception: false }
      : { word: NOT_FINISHED, tone: "neutral", exception: false };
  }
  const { reward, exception } = result;
  if (verdict !== null)
    return {
      word: verdict.label,
      tone: verdict.tone,
      exception: exception !== null,
    };
  if (reward !== null) {
    const solved = reward >= 1;
    return {
      word: solved ? SOLVED : NOT_SOLVED,
      tone: solved ? "good" : "bad",
      exception: exception !== null,
    };
  }
  const plain = { exception: false };
  if (exception !== null) return { word: EXCEPTION, tone: "warn", ...plain };
  return { word: NO_REWARD, tone: "neutral", ...plain };
}

/** The verdict of a trial of a deliverable that gives verdicts, but not it. */
export const NO_VERDICT: { label: string; tone: Tone } = {
  label: "no verdict",
  tone: "neutral",
};

/**
 * The mark beside a trial's status when another trial of its visit took its
 * place, so that the deliverable does not count it.
 */
export const SUPERSEDED = "superseded";

/** The mark beside a trial's status when it also ended on an exception. */
export const WITH_EXCEPTION = EXCEPTION;

/** The word after a trials deliverable's count, unless the project names one. */
export const COUNTED = SOLVED;

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
