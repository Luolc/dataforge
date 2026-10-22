import type { Trial } from "../../api/types";

/** A path in a trial's directory, as the record's paths are. */
export function inTrial(trial: Trial, relative: string): string {
  return trial.path === "" ? relative : `${trial.path}/${relative}`;
}
