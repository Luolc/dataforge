// A trial's test results as a CTRF report holds them
// (https://ctrf.io/docs/specification/test): only what the Tests tab reads.
import { z } from "zod";

const label = z.union([z.string(), z.number(), z.boolean()]);

const test = z.looseObject({
  name: z.string(),
  status: z.enum(["passed", "failed", "skipped", "pending", "other"]),
  // Milliseconds.
  duration: z.number(),
  // From the top-level suite down; an older producer writes one string.
  suite: z.union([z.array(z.string()), z.string()]).optional(),
  rawStatus: z.string().optional(),
  message: z.string().optional(),
  trace: z.string().optional(),
  labels: z.record(z.string(), z.union([label, z.array(label)])).optional(),
});

const report = z.looseObject({
  results: z.looseObject({ tests: z.array(test) }),
});

export type CtrfTest = z.infer<typeof test>;

/** A label's value as text; null when the test has no such label. */
export function labelOf(t: CtrfTest, key: string): string | null {
  const value = t.labels?.[key];
  if (value === undefined) return null;
  return Array.isArray(value) ? value.map(String).join(", ") : String(value);
}

/** The top-level suite a test is grouped under; null when it names none. */
export function suiteOf(t: CtrfTest): string | null {
  const suite = typeof t.suite === "string" ? [t.suite] : (t.suite ?? []);
  return suite[0] ?? null;
}

/**
 * A CTRF file's tests, or why the text is not a CTRF report: not JSON, or
 * the first key that does not fit, as "results.tests[2].status".
 */
export function readCtrf(
  text: string,
): { tests: CtrfTest[] } | { why: string } {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { why: "it is not JSON" };
  }
  const parsed = report.safeParse(data);
  if (parsed.success) return { tests: parsed.data.results.tests };
  const issue = parsed.error.issues[0];
  const where = (issue?.path ?? [])
    .map((key, i) =>
      typeof key === "number"
        ? `[${key.toString()}]`
        : `${i === 0 ? "" : "."}${String(key)}`,
    )
    .join("");
  return {
    why: where === "" ? "it is not an object" : `${where} does not fit`,
  };
}
