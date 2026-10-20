// What a run page reads from the record: the visit's input, each action's
// status and session, and which steps start open.
import {
  type Action,
  type AttemptRecord,
  deliverablesOf,
  type Project,
  type Tone,
  type Visit,
} from "../../../api/types";
import { statusOf } from "../../../ui/status";

/** The statuses the engine gives an action, and how each reads. */
const ACTION_STATUSES: Partial<Record<string, { label: string; tone: Tone }>> =
  {
    ok: { label: "OK", tone: "good" },
    failed: { label: "Failed", tone: "bad" },
  };

/** An action's status as a label and tone; another word is itself, neutral. */
export function actionStatus(status: string): { label: string; tone: Tone } {
  return ACTION_STATUSES[status] ?? { label: status, tone: "neutral" };
}

/**
 * What a visit was handed: the report of the visit before it, which the
 * engine hands on when that visit's transition leads here with `report`;
 * unknown when the record has no graph to tell.
 */
export type Input =
  { kind: "none" } | { kind: "unknown" } | { kind: "report"; from: number };

export function inputOf(record: AttemptRecord, index: number): Input {
  const visit = record.visits[index];
  const before = record.visits[index - 1];
  if (visit === undefined || before === undefined) return { kind: "none" };
  if (!record.graph) return { kind: "unknown" };
  const edge = record.graph.edges.find(
    (e) => e.from === before.state && e.outcome === before.outcome,
  );
  return edge !== undefined && edge.report && edge.to === visit.state
    ? { kind: "report", from: index - 1 }
    : { kind: "none" };
}

export interface Trace {
  path: string;
  format: string | null;
}

/**
 * The trace of an action's session: its own trace, else, as in a record
 * written before actions had one, the trace of the trial the record's
 * deliverables list for this visit and action.
 */
export function traceOf(
  record: AttemptRecord,
  index: number,
  action: Action,
): Trace | null {
  if (action.trace) return action.trace;
  const names = Object.keys(action.outputs);
  for (const item of deliverablesOf(record)) {
    if (item.visit_index !== index) continue;
    const trial = item.trials?.find((t) => names.includes(t.output));
    if (trial?.trace) return trial.trace;
  }
  return null;
}

/** Whether an action ran an agent: an agent action, or one with a trace. */
export function isSession(
  record: AttemptRecord,
  index: number,
  action: Action,
): boolean {
  return action.kind === "agent" || traceOf(record, index, action) !== null;
}

/**
 * Which actions start open: the failed ones and the visit's first session.
 * The output starts open when its outcome's tone is bad.
 */
export function startsOpen(
  project: Project,
  record: AttemptRecord,
  index: number,
): { actions: boolean[]; output: boolean } {
  const visit: Visit | undefined = record.visits[index];
  if (visit === undefined) return { actions: [], output: false };
  const first = visit.actions.findIndex((a) => isSession(record, index, a));
  return {
    actions: visit.actions.map(
      (a, i) => i === first || actionStatus(a.status).tone === "bad",
    ),
    output: statusOf(project, visit.outcome).tone === "bad",
  };
}
