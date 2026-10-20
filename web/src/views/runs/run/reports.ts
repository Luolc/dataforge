// Which of a visit's outputs a run page renders as Markdown, read only from
// what the engine records for every dataset: an action's kind and the paths
// of its outputs.
import type { Action, Visit } from "../../../api/types";

/** An output of an action: its name and its path under the attempt's root. */
export interface Output {
  name: string;
  path: string;
}

/** An action's outputs whose path ends in `.md`, in the action's order. */
export function markdownOf(action: Action): Output[] {
  return Object.entries(action.outputs)
    .filter(([, path]) => path.toLowerCase().endsWith(".md"))
    .map(([name, path]) => ({ name, path }));
}

/**
 * The reports the engine itself wrote in a visit, the Markdown outputs of its
 * `report` actions in order, such as a rubric's report and the feedback a
 * visit hands back on its transition.
 */
export function reportsOf(visit: Visit): Output[] {
  return visit.actions.filter((a) => a.kind === "report").flatMap(markdownOf);
}
