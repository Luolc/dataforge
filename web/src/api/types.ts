// The API's JSON (design-viz.md section 4) and the record it serves as written
// (design.md section 5, "The record"), parsed from unknown at the client.
// Objects keep keys these schemas do not name, so a newer record still loads.
import { z } from "zod";

const TONES = ["good", "bad", "warn", "neutral"] as const;
export type Tone = (typeof TONES)[number];

/**
 * How a value read from a file stands: found (JSON null among them); not
 * run, its file or the tab's version it is in not there; missing, the file
 * there but refused, not JSON or without the keys.
 */
const READS = ["found", "not run", "missing"] as const;
export type Read = (typeof READS)[number];

const FORMAT = z.enum(["text", "number", "usd", "duration", "time"]);

export const project = z.looseObject({
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
      format: FORMAT,
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
  // The definition snapshot's path in an attempt's directory.
  definition: z.string().nullable().optional(),
  // Whether the project names a resource monitor database.
  monitor: z.boolean().optional(),
  // The attempt page's tabs of Markdown files, each found in the
  // directories of an attempt that match its versions: one part, the tab's
  // own file, or two or more, each an entry of the tab's column.
  tabs: z
    .array(
      z.looseObject({
        name: z.string(),
        title: z.string(),
        versions: z.array(z.string()),
        parts: z.array(
          z.looseObject({
            name: z.string(),
            title: z.string(),
            file: z.string(),
            fields: z.array(
              z.looseObject({ title: z.string(), format: FORMAT }),
            ),
          }),
        ),
      }),
    )
    .optional(),
  // What a data item's page may put in a queue.
  actions: z
    .array(
      z.looseObject({ name: z.string(), label: z.string(), queue: z.string() }),
    )
    .optional(),
});
export type Project = z.infer<typeof project>;
export type Column = Project["columns"][number];
export type ProjectTab = NonNullable<Project["tabs"]>[number];
export type TabPart = ProjectTab["parts"][number];
export const projects = z.array(project);

// Each tab of the project, with the versions an attempt has of it, the first
// shown by default, and the value of each field of each of its parts in each:
// read found, value the JSON value, null among them; else value null and
// reason why there is none.
export const tabVersions = z.array(
  z.looseObject({
    name: z.string(),
    versions: z.array(
      z.looseObject({
        dir: z.string(),
        parts: z.array(
          z.looseObject({
            name: z.string(),
            fields: z.array(
              z.looseObject({
                read: z.enum(READS),
                value: z.unknown(),
                reason: z.string().nullable(),
              }),
            ),
          }),
        ),
      }),
    ),
  }),
);
export type TabVersions = z.infer<typeof tabVersions>[number];
export type TabVersion = TabVersions["versions"][number];

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
  // How each column with a file read its value; null for a column of the
  // record.
  reads: z.array(z.enum(READS).nullable()).nullable().optional(),
  live: z.enum(LIVE).nullable().optional(),
  // Whether the item's lock is held now; null when the project names none.
  held: z.boolean().nullable().optional(),
  error: z.string().nullable(),
});
export type AttemptRow = z.infer<typeof attemptRow>;
export const attemptRows = z.array(attemptRow);

// How one attempt stands now (/live): live and held as its listing row has
// them, and the visit it is in while it runs.
export const liveInfo = z.looseObject({
  live: z.enum(LIVE).nullable(),
  held: z.boolean().nullable(),
  current: z
    .looseObject({
      state: z.string(),
      visit: z.number(),
      index: z.number(),
      recorded: z.boolean(),
      since: z.string().nullable(),
    })
    .nullable(),
});
export type LiveInfo = z.infer<typeof liveInfo>;
export type Current = NonNullable<LiveInfo["current"]>;

// A number a person may set when they submit an action, from min to max.
const actionParam = z.looseObject({
  name: z.string(),
  label: z.string(),
  min: z.number(),
  max: z.number(),
  default: z.number(),
  format: z.enum(["number", "usd"]),
});
export type ActionParam = z.infer<typeof actionParam>;

// Each of a data item's actions, why it may not be submitted now (null when
// it may) and the numbers a person may set for it.
export const actionStates = z.array(
  z.looseObject({
    name: z.string(),
    label: z.string(),
    queue: z.string(),
    reason: z.string().nullable(),
    params: z.array(actionParam),
  }),
);
export type ActionState = z.infer<typeof actionStates>[number];

// A job of the queue, as a page may show it; times in seconds since the
// epoch.
const job = z.looseObject({
  id: z.number(),
  queue: z.string(),
  labels: z.record(z.string(), z.string()),
  state: z.string(),
  memory_mib: z.number(),
  queued_at: z.number(),
  started_at: z.number().nullable(),
  ended_at: z.number().nullable(),
  exit_code: z.number().nullable(),
  cancel_requested_at: z.number().nullable(),
});
export type Job = z.infer<typeof job>;

/** What a write answers: the job it queued or canceled. */
export const written = z.looseObject({ job });

/**
 * The queue page's read: each queue, and the jobs open and the last
 * ended_limit that ended.
 */
export const queueRead = z.looseObject({
  queues: z.array(
    z.looseObject({
      name: z.string(),
      limit: z.number(),
      running: z.number(),
      queued: z.number(),
    }),
  ),
  max_running: z.number(),
  ended_limit: z.number(),
  // How many jobs have ended, those that jobs leaves out included.
  ended: z.number(),
  jobs: z.array(job),
});
export type QueueRead = z.infer<typeof queueRead>;

export const listing = z.looseObject({
  entries: z.array(
    z.looseObject({
      name: z.string(),
      type: z.enum(["file", "dir", "symlink", "other"]),
      size: z.number().nullable(),
      // A file the source lists by its name and size but does not serve.
      listed_only: z.boolean().optional(),
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

// A trial that has started and not been recorded yet has no id, trace or
// result (design.md section 5, "The record").
const trial = z.looseObject({
  id: z.string().nullable(),
  output: z.string(),
  path: z.string(),
  trace: z
    .looseObject({ path: z.string(), format: z.string().nullable() })
    .nullable(),
  result: z
    .looseObject({
      reward: z.number().nullable(),
      rewards: z.record(z.string(), z.number()),
      exception: z.string().nullable(),
    })
    .nullable(),
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

/**
 * The visit a running attempt is in, when it fits the record as read: the
 * record's last visit when it holds it, else the one after its last. A record
 * read before or after /live may not fit, and then the page shows none.
 */
export function currentOf(
  record: AttemptRecord,
  info: LiveInfo | null,
): Current | null {
  const current = info?.current ?? null;
  if (current === null) return null;
  const visits = record.visits.length;
  const fits = current.recorded
    ? current.index === visits - 1 &&
      record.visits[current.index]?.state === current.state &&
      record.visits[current.index]?.outcome === null
    : current.index === visits;
  return fits ? current : null;
}
