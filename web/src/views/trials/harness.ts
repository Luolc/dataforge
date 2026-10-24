// A trial's harness, the agent program that ran it: the record's, else, in
// the viewer, the agent_info of the result.json Harbor writes in the trial's
// directory. The one place that names each harness for people.
import { z } from "zod";

import type { Harness, Trial } from "../../api/types";
import { useFile } from "../files/file";
import { inTrial } from "./path";

/** Each harness's name as its maker writes it, by its name in Harbor. */
const NAMES: Record<string, string> = {
  "claude-code": "Claude Code",
  oracle: "Oracle",
};

/** "Claude Code 2.1.296"; a name not in NAMES shows as it is. */
export function harnessName({ name, version }: Harness): string {
  const shown = NAMES[name] ?? name;
  return version === null ? shown : `${shown} ${version}`;
}

const result = z.looseObject({
  agent_info: z.looseObject({
    name: z.string(),
    version: z.string().nullable().default(null),
  }),
});

/** The harness a Harbor result.json gives; null when it gives none. */
function harnessOf(text: string): Harness | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  const read = result.safeParse(parsed);
  return read.success ? read.data.agent_info : null;
}

/**
 * The trial's harness: its record's, else, when read is true, its
 * result.json's; null while it loads and when neither gives one.
 */
export function useHarness(
  project: string,
  attempt: string,
  trial: Trial,
  read: boolean,
): Harness | null {
  const recorded = trial.harness;
  const file = useFile(
    project,
    attempt,
    recorded === undefined && read ? inTrial(trial, "result.json") : null,
  );
  if (recorded !== undefined) return recorded;
  return file.state === "ok" && file.data.kind === "text"
    ? harnessOf(file.data.text)
    : null;
}
