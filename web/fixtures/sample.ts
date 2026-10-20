// The synthetic attempts of fixtures/attempts as the frontend's tests read
// them, and a project like project.yaml, as /api/projects would serve it.
import {
  type AttemptRecord,
  type Current,
  type LiveInfo,
  type Project,
  record,
} from "../src/api/types";
import passed from "./attempts/batch-1/task-0001/record.json";
import tooEasy from "./attempts/batch-1/task-0002/record.json";
import exhausted from "./attempts/batch-1/task-0003/record.json";
import running from "./attempts/batch-2/task-0005/record.json";
import raised from "./attempts/batch-2/task-0006/record.json";
import resumed from "./attempts/resume/task-0401/record.json";

export const sampleProject: Project = {
  id: "sample",
  name: "Sample dataset",
  color: "#e5a54b",
  states: { solve: { title: "Pass@k" } },
  statuses: {
    passed: { label: "Passed", tone: "good" },
    exhausted: { label: "Exhausted", tone: "bad" },
    fail: { label: "Fail", tone: "bad" },
  },
  item: { depth: 2, title: "Task" },
  columns: [],
  session: { thinking: "collapsed" },
};

/** value, or a thrown error when it is missing, for tests. */
export function must<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error("missing");
  return value;
}

export const records = {
  passed: record.parse(passed),
  tooEasy: record.parse(tooEasy),
  exhausted: record.parse(exhausted),
  running: record.parse(running),
  raised: record.parse(raised),
  resumed: record.parse(resumed),
} satisfies Record<string, AttemptRecord>;

/**
 * passed's first four runs as a running attempt's record: check passed, so
 * it is in review, which the record does not hold yet.
 */
export const inReview: AttemptRecord = {
  ...records.passed,
  status: null,
  visits: records.passed.visits.slice(0, 4),
  deliverables: [],
};

/** When inReview's check ended, so its review began. */
export const reviewSince = "2026-03-02T09:43:39+00:00";

/** /live of an attempt as inReview while it runs, with current changed. */
export function runningIn(current: Partial<Current> = {}): LiveInfo {
  return {
    live: "running",
    held: true,
    current: {
      state: "review",
      visit: 1,
      index: 4,
      recorded: false,
      since: reviewSince,
      ...current,
    },
  };
}

/** /live of an attempt that may have stopped: no current. */
export const mayHaveStopped: LiveInfo = {
  live: "may have stopped",
  held: false,
  current: null,
};
