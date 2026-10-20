// The API's JSON (design-viz.md section 4) and the record it serves as written
// (design.md section 5, "The record"), parsed from unknown at the client.
// Objects keep keys these schemas do not name, so a newer record still loads.
import { z } from "zod";

const TONES = ["good", "bad", "warn", "neutral"] as const;
export type Tone = (typeof TONES)[number];

const project = z.looseObject({
  id: z.string(),
  name: z.string(),
  color: z.string(),
  states: z.record(z.string(), z.looseObject({ title: z.string() })),
  statuses: z.record(
    z.string(),
    z.looseObject({ label: z.string(), tone: z.enum(TONES) }),
  ),
  item: z.looseObject({ depth: z.number(), title: z.string() }),
  columns: z.array(
    z.looseObject({
      title: z.string(),
      field: z.array(z.string()),
      format: z.enum(["text", "number", "usd", "duration", "time"]),
    }),
  ),
  session: z.looseObject({
    thinking: z.enum(["collapsed", "expanded", "hidden"]),
  }),
  not_ended: z.array(z.string()).nullable().optional(),
  deliverables: z
    .array(
      z.looseObject({ name: z.string(), kind: z.string(), title: z.string() }),
    )
    .optional(),
  // A trial's diff, relative to its directory.
  trials: z.looseObject({ diff: z.string() }).nullable().optional(),
});
export type Project = z.infer<typeof project>;
export type Column = Project["columns"][number];
export const projects = z.array(project);

/** How an attempt that has not ended stands; null once it has ended. */
const LIVE = ["running", "may have stopped", "not ended"] as const;
export type Live = (typeof LIVE)[number];

export const attemptRow = z.looseObject({
  id: z.string(),
  item: z.string(),
  modified: z.string().nullable(),
  status: z.string().nullable(),
  reason: z.string().nullable(),
  cost_usd: z.number().nullable(),
  started_at: z.string().nullable(),
  ended_at: z.string().nullable(),
  rounds: z
    .array(
      z.looseObject({
        round: z.number(),
        state: z.string().nullable(),
        outcome: z.string().nullable(),
      }),
    )
    .nullable(),
  values: z.array(z.unknown()).nullable(),
  live: z.enum(LIVE).nullable().optional(),
  // Whether the item's lock is held now; null when the project names none.
  held: z.boolean().nullable().optional(),
  error: z.string().nullable(),
});
export type AttemptRow = z.infer<typeof attemptRow>;
export const attemptRows = z.array(attemptRow);

export const listing = z.looseObject({
  entries: z.array(
    z.looseObject({
      name: z.string(),
      type: z.enum(["file", "dir", "symlink", "other"]),
      size: z.number().nullable(),
    }),
  ),
});
export type Listing = z.infer<typeof listing>;

const action = z.looseObject({
  kind: z.string(),
  status: z.string(),
  outputs: z.record(z.string(), z.string()),
  started_at: z.string(),
  ended_at: z.string(),
  cost_usd: z.number(),
  summary: z.string().nullable().optional(),
  trace: z
    .looseObject({ path: z.string(), format: z.string().nullable() })
    .nullable()
    .optional(),
  session: z.unknown().optional(),
});
export type Action = z.infer<typeof action>;

const visit = z.looseObject({
  state: z.string(),
  visit: z.number(),
  round: z.number().optional(),
  outcome: z.string().nullable(),
  data: z.record(z.string(), z.unknown()),
  mark: z.string().nullable(),
  actions: z.array(action),
});
export type Visit = z.infer<typeof visit>;

// One time the engine went on with the attempt (design.md, "Resuming an
// attempt"): the first kept visits came before it, and discarded holds the
// visits it left out, each with where their outputs were kept.
const resumption = z.looseObject({
  at: z.string(),
  kept: z.number(),
  discarded: z.array(
    visit.extend({
      path: z.string().nullable(),
      reason: z.string().nullable(),
    }),
  ),
  carried_usd: z.number(),
  reason: z.string().nullable(),
});
export type Resumption = z.infer<typeof resumption>;

const graphState = z.looseObject({
  name: z.string(),
  kind: z.string(),
  max_visits: z.number(),
  on_exhausted: z.string(),
  max_cost_usd: z.number().nullable(),
});

const graphEdge = z.looseObject({
  from: z.string(),
  outcome: z.string(),
  to: z.string().nullable(),
  end: z.string().nullable(),
  report: z.boolean(),
  mark: z.string().nullable(),
  end_on_last_visit: z.boolean(),
});

const graph = z.looseObject({
  start: z.string(),
  states: z.array(graphState),
  edges: z.array(graphEdge),
});

const source = {
  state: z.string(),
  output: z.string().nullable(),
  visit: z.number().nullable(),
  visit_index: z.number().nullable(),
  path: z.string().nullable(),
  reason: z.string().nullable(),
};

const trial = z.looseObject({
  id: z.string(),
  output: z.string(),
  path: z.string(),
  trace: z
    .looseObject({ path: z.string(), format: z.string().nullable() })
    .nullable(),
  result: z.looseObject({
    reward: z.number().nullable(),
    rewards: z.record(z.string(), z.number()),
    exception: z.string().nullable(),
  }),
});
export type Trial = z.infer<typeof trial>;

// One schema for every kind: a kind adds its own keys (a report its table,
// trials k, value and trials), and a kind this viewer does not know still
// loads, to be shown as a card.
const deliverable = z.looseObject({
  name: z.string(),
  kind: z.string(),
  title: z.string(),
  ...source,
  table: z.looseObject(source).nullable().optional(),
  k: z.number().optional(),
  value: z
    .looseObject({
      output: z.string().nullable(),
      field: z.array(z.string()),
      value: z.unknown(),
      reason: z.string().nullable(),
    })
    .nullable()
    .optional(),
  trials: z.array(trial).optional(),
});
export type Deliverable = z.infer<typeof deliverable>;

// The keys Attempt.to_json() gained after its first version (round, budget,
// a visit's round, an action's summary, trace and session, deliverables,
// graph, resumptions) are optional,
// so a record an older dataforge wrote still loads (design-viz.md section 5,
// "Older records"). visits comes first, so a record without it is reported
// as having no visits, the key a page cannot be drawn without.
export const record = z.looseObject({
  visits: z.array(visit),
  status: z.string().nullable(),
  reason: z.string().nullable(),
  kept_visit: z.number().nullable(),
  round: z.number().optional(),
  budget: z
    .looseObject({
      max_usd: z.number().nullable(),
      cost_usd: z.number(),
      stopped: z.string().nullable(),
    })
    .optional(),
  final: z.record(z.string(), visit),
  deliverables: z.array(deliverable).optional(),
  graph: graph.nullable().optional(),
  resumptions: z.array(resumption).optional(),
});
export type AttemptRecord = z.infer<typeof record>;

/**
 * A record file as the attempt page reads it: of the record's shape, or
 * another JSON object, shown as written, with why it is not of that shape.
 */
export type ReadRecord =
  | { shape: "record"; record: AttemptRecord }
  | { shape: "other"; raw: object; why: string };

/** Read a record file's JSON; one that is not a JSON object is an error. */
export function readRecord(raw: unknown): ReadRecord {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw))
    throw new Error("The record is not a JSON object.");
  const parsed = record.safeParse(raw);
  if (parsed.success) return { shape: "record", record: parsed.data };
  const issue = parsed.error.issues[0];
  const where = issue?.path.join(".") ?? "";
  const missing =
    issue?.code === "invalid_type" && issue.message.endsWith("undefined");
  return {
    shape: "other",
    raw,
    why: missing ? `it has no ${where}` : `${where}: ${issue?.message ?? ""}`,
  };
}

/** The record's deliverables; none when it has no such key. */
export function deliverablesOf(record: AttemptRecord): Deliverable[] {
  return record.deliverables ?? [];
}
