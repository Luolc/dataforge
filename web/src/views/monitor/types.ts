// The Monitor routes' JSON (design-viz.md section 4, "The API"), parsed from
// unknown at the client.
import { z } from "zod";

const api = (p: string) => `/api/projects/${encodeURIComponent(p)}/monitor`;

const query = (params: Record<string, string>) =>
  new URLSearchParams(params).toString();

export const monitorUrls = {
  runs: (p: string) => `${api(p)}/runs`,
  window: (p: string, run: number, from: number | null, to: number | null) =>
    `${api(p)}/window?${query({
      run: run.toString(),
      ...(from !== null && { t0: from.toString() }),
      ...(to !== null && { t1: to.toString() }),
    })}`,
  curve: (p: string, run: number, from: number, to: number, of: Selected) =>
    `${api(p)}/curve?${query({
      run: run.toString(),
      t0: from.toString(),
      t1: to.toString(),
      [of.kind]: of.id,
    })}`,
};

/** A route's answer when the database does not exist yet. */
export const noDatabase = z.looseObject({
  error: z.literal("no database"),
  db: z.string(),
});

/** What the page draws a curve of besides the machine and the batch. */
export interface Selected {
  kind: "attempt" | "container";
  id: string;
}

const num = z.number().nullable();

export const runs = z.array(
  z.looseObject({
    run_id: z.number(),
    started_at: z.number(),
    ended_at: num,
    stop_reason: z.string().nullable(),
    source: z.string(),
  }),
);
export type Run = z.infer<typeof runs>[number];

const bucket = { t: z.number() };

const bar = z.looseObject({
  container_id: z.string(),
  start_t: z.number(),
  last_t: z.number(),
  image: z.string(),
  item: z.string().nullable(),
  attempt: z.string().nullable(),
  state: z.string().nullable(),
  visit: num,
  trial: z.string().nullable(),
  exit_code: num,
  open: z.union([z.number(), z.boolean()]).transform(Boolean),
});
export type Bar = z.infer<typeof bar>;

export const window = z.looseObject({
  run_id: z.number(),
  t0: z.number(),
  t1: z.number(),
  cpus: z.number(),
  mem_total_bytes: z.number(),
  machine: z.array(
    z.looseObject({
      ...bucket,
      cpu_cores_avg: num,
      cpu_cores_max: num,
      mem_used_bytes_avg: num,
      mem_used_bytes_max: num,
      swap_used_bytes_max: num,
      load1_max: num,
    }),
  ),
  batch: z.array(
    z.looseObject({
      ...bucket,
      attempts_max: num,
      sessions_max: num,
      pool_bytes: num,
      pool_reserved_bytes_avg: num,
      pool_reserved_bytes_max: num,
      pool_waiting_max: num,
    }),
  ),
  pauses: z.array(z.looseObject({ start_t: z.number(), end_t: num })),
  bars: z.array(bar),
  bars_total: z.number(),
  bars_until: z.number().nullable(),
});
export type Window = z.infer<typeof window>;
export type Pause = Window["pauses"][number];

export const curve = z.array(
  z.looseObject({
    ...bucket,
    cpu_cores_avg: num,
    cpu_cores_max: num,
    mem_bytes_avg: num,
    mem_bytes_max: num,
  }),
);
export type Curve = z.infer<typeof curve>;
