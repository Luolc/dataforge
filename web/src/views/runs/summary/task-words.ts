// The Task card's own words (L37): how a task.toml that did not read stands,
// and the names of what dataforge.task defines, its suites, the kind of the
// project's own tests and the types of a principle. A dataset's kinds take
// their labels from the project's trials.kinds.
import type { Read, Tone } from "../../../api/types";
import { NOT_RUN, viewerStatus } from "../../../ui/status";

/** A task.toml not there, which the attempt never reached, or refused. */
export const TASK_READ: Record<
  Exclude<Read, "found">,
  { label: string; tone: Tone }
> = {
  "not run": { label: NOT_RUN, tone: "neutral" },
  missing: viewerStatus.unreadable,
};

/** dataforge.task's SUITES, in order, each with its title. */
export const SUITES = [
  { suite: "f2p", title: "Fail to pass" },
  { suite: "p2p", title: "Pass to pass" },
] as const;

/** The kind of p2p that counts the project's own tests. */
export const PROJECT_KIND = "project";
export const PROJECT_LABEL = "Project's own";

/** A principle's type, as dataforge.task's TYPES names it. */
export const PRINCIPLE_TYPES: Record<string, string> = {
  best_practice: "Best practice",
  trade_off: "Trade-off",
};

/** What a value the file does not record shows. */
export const NOT_RECORDED = "Not recorded";

/** Why a suite has no counts. */
export const noSuite = (suite: string, legacy: boolean) =>
  legacy
    ? `The format before tests records no ${suite}.`
    : `This task.toml records no ${suite}.`;

/** Why a principle's tests are not known. */
export const LEGACY_TESTS =
  "The format before tests does not record which tests check it.";
