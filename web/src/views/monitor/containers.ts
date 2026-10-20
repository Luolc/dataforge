// The containers' bars as the page groups, stacks and names them.
import type { Project } from "../../api/types";
import type { Bar } from "./types";

export interface Group {
  key: string;
  attempt: string | null;
  label: string;
  bars: Bar[];
}

/** The bars by attempt, each group in the order the query gives its bars,
 * the groups by when their first container started. */
export function groupBars(bars: Bar[]): Group[] {
  const groups = new Map<string, Group>();
  for (const bar of bars) {
    const key = bar.attempt ?? `item:${bar.item ?? ""}`;
    let group = groups.get(key);
    if (group === undefined) {
      group = {
        key,
        attempt: bar.attempt,
        label: bar.attempt ?? `${bar.item ?? "No item"} · no attempt`,
        bars: [],
      };
      groups.set(key, group);
    }
    group.bars.push(bar);
  }
  const first = (g: Group) => Math.min(...g.bars.map((b) => b.start_t));
  return [...groups.values()].sort((a, b) => first(a) - first(b));
}

/** Each bar's lane, in the order given: the first lane free when it starts. */
export function lanes(bars: Bar[]): number[] {
  const ends: number[] = [];
  return bars.map((bar) => {
    let lane = ends.findIndex((end) => end < bar.start_t);
    if (lane === -1) lane = ends.length;
    ends[lane] = bar.last_t;
    return lane;
  });
}

/** Whether a container ended in failure: a non-zero exit code. */
export const failed = (bar: Bar) =>
  bar.exit_code !== null && bar.exit_code !== 0;

/** A container's name on the page: its state's title, visit and trial. */
export function barLabel(project: Project, bar: Bar): string {
  const state =
    bar.state === null
      ? "No state"
      : (project.states[bar.state]?.title ?? bar.state);
  const parts = [state];
  if (bar.visit !== null) parts.push(`visit ${bar.visit.toString()}`);
  if (bar.trial !== null) parts.push(bar.trial);
  return parts.join(" · ");
}

/** How a container ended, in words. */
export function ending(bar: Bar): string {
  if (bar.open)
    return "No end seen: still running, or the sampler stopped first";
  if (bar.exit_code === null) return "Ended, exit code not known";
  return `Exited with ${bar.exit_code.toString()}`;
}
