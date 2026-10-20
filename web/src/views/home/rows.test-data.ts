// Synthetic listing rows for the home page's tests.
import type { Project } from "../../api/types";
import type { AttemptRow } from "./items";

/** A readable, ended attempt of an item, with fields to change. */
export function row(id: string, fields: Partial<AttemptRow> = {}): AttemptRow {
  return {
    id,
    item: id.split("/")[0] ?? id,
    modified: "2026-03-02T12:00:00+00:00",
    status: "passed",
    reason: null,
    cost_usd: 1,
    started_at: "2026-03-02T09:00:00+00:00",
    ended_at: "2026-03-02T10:00:00+00:00",
    rounds: [{ round: 1, state: "solve", outcome: "pass" }],
    values: [],
    live: null,
    error: null,
    ...fields,
  };
}

/** A row whose record cannot be read, as the listing gives it. */
export function unreadable(id: string): AttemptRow {
  return row(id, {
    modified: null,
    status: null,
    cost_usd: null,
    started_at: null,
    ended_at: null,
    rounds: null,
    values: null,
    error: "the record: not found",
  });
}

export const project: Project = {
  id: "sample",
  name: "Sample dataset",
  color: "#e5a54b",
  states: { solve: { title: "Pass@k" } },
  statuses: {
    passed: { label: "Passed", tone: "good" },
    too_easy: { label: "Too easy", tone: "warn" },
    exhausted: { label: "Exhausted", tone: "bad" },
    pass: { label: "Pass", tone: "good" },
  },
  item: { depth: 1, title: "Task" },
  columns: [],
  session: { thinking: "collapsed" },
};
