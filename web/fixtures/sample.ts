// The synthetic attempts of fixtures/attempts as the frontend's tests read
// them, and a project like project.yaml, as /api/projects would serve it.
import { type AttemptRecord, type Project, record } from "../src/api/types";
import passed from "./attempts/batch-1/task-0001/record.json";
import tooEasy from "./attempts/batch-1/task-0002/record.json";
import exhausted from "./attempts/batch-1/task-0003/record.json";
import running from "./attempts/batch-2/task-0005/record.json";

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
  columns: [{ title: "Status", field: ["status"], format: "text" }],
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
} satisfies Record<string, AttemptRecord>;
