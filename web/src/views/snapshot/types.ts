// A snapshot page's file as the backend serves it once its check passes it
// (design-viz.md section 4, "The project file").
import { z } from "zod";

const tone = z.enum(["good", "bad", "warn", "neutral"]);

const column = z.looseObject({
  name: z.string(),
  title: z.string(),
  format: z
    .enum(["text", "number", "usd", "duration", "time", "status"])
    .optional(),
  filter: z.boolean().optional(),
});

const row = z.looseObject({
  id: z.string(),
  values: z.record(z.string(), z.union([z.string(), z.number(), z.null()])),
  dir: z
    .looseObject({
      root: z.string(),
      path: z.string(),
      open: z.string().optional(),
    })
    .optional(),
});

const step = z.looseObject({
  name: z.string(),
  title: z.string(),
  value: z.number(),
  unit: z.string().optional(),
  of: z.number().optional(),
  note: z.string().optional(),
  generated_at: z.string().optional(),
  page: z.string().optional(),
  entries: z
    .looseObject({
      id_title: z.string(),
      columns: z.array(column),
      rows: z.array(row),
    })
    .optional(),
});

export const snapshot = z.looseObject({
  version: z.literal(1),
  generated_at: z.string(),
  statuses: z
    .record(z.string(), z.looseObject({ label: z.string(), tone }))
    .optional(),
  steps: z.array(step),
});

export type Snapshot = z.infer<typeof snapshot>;
export type Step = Snapshot["steps"][number];
export type Entries = NonNullable<Step["entries"]>;
export type SnapshotColumn = Entries["columns"][number];
export type Row = Entries["rows"][number];

/** Why the backend refused the snapshot. */
export const reason = z.looseObject({ reason: z.string() });

/** What the backend answers for a snapshot that is not there yet. */
export const noSnapshot = z.looseObject({
  error: z.literal("no snapshot"),
  file: z.string().nullable(),
});
