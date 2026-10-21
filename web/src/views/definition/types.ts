// The definition snapshot a dataset writes into an attempt's directory as it
// starts (design-viz.md section 4, "The project file"): what each state's
// agents are given. It is content from an attempt (invariant 3), read through
// the files route; objects keep keys these schemas do not name.
import { z } from "zod";

const variable = z.looseObject({
  name: z.string(),
  source: z.string(),
  /** The text put in its place for this attempt, as fixed when it started. */
  value: z.string().optional(),
  /** True when the value changes from round to round. */
  per_round: z.boolean().optional(),
});

const criterion = z.looseObject({
  id: z.string(),
  question: z.string(),
  check: z.string().optional(),
  pass_if: z.string().optional(),
  fail_if: z.string().optional(),
});

const textEntry = z.looseObject({
  kind: z.literal("text"),
  title: z.string(),
  note: z.string().optional(),
  format: z.string(),
  template: z.enum(["dollar", "jinja"]).nullable(),
  variables: z.array(variable),
  text: z.string(),
});

const rubricEntry = z.looseObject({
  kind: z.literal("rubric"),
  title: z.string(),
  note: z.string().optional(),
  reads: z.array(z.string()).optional(),
  rubric: z.looseObject({ system: z.string(), criteria: z.array(criterion) }),
});

/** An entry of a kind this viewer does not know: shown by title and kind. */
const otherEntry = z.looseObject({ kind: z.string(), title: z.string() });

// Checked by its kind, so that what does not fit is named at its own key.
const entry = otherEntry.transform((e, ctx) => {
  const schema =
    e.kind === "text" ? textEntry : e.kind === "rubric" ? rubricEntry : null;
  if (schema === null) return e;
  const parsed = schema.safeParse(e);
  if (parsed.success) return parsed.data;
  for (const issue of parsed.error.issues) ctx.addIssue({ ...issue });
  return z.NEVER;
});

const workflow = z.looseObject({
  path: z.string(),
  format: z.string(),
  text: z.string(),
});

const definition = z.looseObject({
  version: z.looseObject({
    commit: z.string().nullable(),
    dirty: z.boolean().nullable(),
    prompt_version: z.string().nullable(),
  }),
  workflow: workflow.nullable(),
  stages: z.array(
    z.looseObject({
      state: z.string(),
      entries: z.array(entry),
    }),
  ),
});

export type Definition = z.infer<typeof definition>;
export type Stage = Definition["stages"][number];
export type Entry = Stage["entries"][number];
export type TextEntry = z.infer<typeof textEntry>;
export type RubricEntry = z.infer<typeof rubricEntry>;
export type Criterion = z.infer<typeof criterion>;
export type Workflow = z.infer<typeof workflow>;
export type Variable = z.infer<typeof variable>;

export const isText = (e: Entry): e is TextEntry => e.kind === "text";
export const isRubric = (e: Entry): e is RubricEntry => e.kind === "rubric";

/**
 * A snapshot file as a page reads it: of the snapshot's shape, or not, with
 * why and the file as text.
 */
export type ReadDefinition =
  | { shape: "definition"; definition: Definition }
  | { shape: "other"; text: string; why: string };

/** Read a snapshot file's text, as readRecord reads a record. */
export function readDefinition(text: string): ReadDefinition {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { shape: "other", text, why: "it is not JSON" };
  }
  const parsed = definition.safeParse(raw);
  if (parsed.success) return { shape: "definition", definition: parsed.data };
  const issue = parsed.error.issues[0];
  const where = issue?.path.join(".") ?? "";
  const missing =
    issue?.code === "invalid_type" && issue.message.endsWith("undefined");
  return {
    shape: "other",
    text,
    why: missing
      ? `it has no ${where}`
      : `${where !== "" ? where : "the file"}: ${issue?.message ?? ""}`,
  };
}
