// The deliverables a project declares, set beside a record's (design-viz.md
// section 5, "Attempt page"): a record has none until its attempt ends.
import type { AttemptRecord, Deliverable, Project } from "../../api/types";
import { runCount } from "../../ui/format";
import { attemptStatus, isEnding, stateTitle, statusOf } from "../../ui/status";

/**
 * The record as the page shows it: each deliverable titled as the project
 * titles the one of its name, a deliverable the project does not list keeping
 * the title it recorded, and each one without a path giving why in the
 * viewer's words (notReached); the record keeps the engine's.
 */
export function asShown(
  project: Project,
  record: AttemptRecord,
): AttemptRecord {
  const have = record.deliverables;
  if (have === undefined) return record;
  const titles = new Map(
    (project.deliverables ?? []).map((d) => [d.name, d.title]),
  );
  return {
    ...record,
    deliverables: have.map((d) => ({
      ...d,
      title: titles.get(d.name) ?? d.title,
      reason: d.path === null ? notReached(project, record, d) : d.reason,
    })),
  };
}

/**
 * Why a deliverable of the record has no path, after "Not run: ", by the
 * title of the state it comes from: the attempt never reached the state, or
 * has not yet while it runs; the state never finished a run, or has not
 * finished its first yet; its only runs did not finish (isEnding), the last
 * of them named; it ran only after the run the attempt kept; its
 * runs are not the one the deliverable takes; or the run it takes wrote no
 * such output.
 */
function notReached(
  project: Project,
  record: AttemptRecord,
  item: Deliverable,
): string {
  const title = stateTitle(project, item.state);
  const ended = attemptStatus(project, record) !== null;
  if (item.visit_index === null) {
    const mine = record.visits.flatMap((v, i) =>
      v.state === item.state ? [{ v, i }] : [],
    );
    if (mine.length === 0)
      return ended
        ? `the attempt never reached ${title}.`
        : `the attempt has not reached ${title} yet.`;
    if (mine.every(({ v }) => v.outcome === null))
      return ended
        ? `${title} never finished a run.`
        : `${title} has not finished a run yet.`;
    const unfinished = mine.findLast(({ v }) => isEnding(v.outcome));
    if (
      unfinished !== undefined &&
      mine.every(({ v }) => v.outcome === null || isEnding(v.outcome))
    ) {
      const how = statusOf(project, unfinished.v.outcome).label;
      return `${title} · run ${(unfinished.i + 1).toString()} did not finish (${how}).`;
    }
    const kept = record.kept_visit;
    if (kept !== null && mine.every(({ i }) => i > kept))
      return `${title} ran only after the run the attempt kept, run ${(kept + 1).toString()}.`;
    const runs = `${title}'s ${runCount(mine.length)} ${mine.length === 1 ? "does" : "do"}`;
    return `${runs} not include the one this takes${ended ? "" : " yet"}.`;
  }
  const run = (item.visit_index + 1).toString();
  return `${title} · run ${run} wrote no ${item.output ?? "output"}.`;
}

/**
 * Each deliverable the project declares that the record lacks, as an entry
 * with no path and the reason it is missing; none for a record with no
 * deliverables key, which an older dataforge wrote.
 */
export function declaredMissing(
  project: Project,
  record: AttemptRecord,
): Deliverable[] {
  const have = record.deliverables;
  if (have === undefined) return [];
  const reason =
    attemptStatus(project, record) === null
      ? "the attempt has not reached the state that makes it yet."
      : "the attempt ended without it.";
  return (project.deliverables ?? [])
    .filter((d) => !have.some((h) => h.name === d.name))
    .map((d) => ({
      name: d.name,
      kind: d.kind,
      title: d.title,
      state: "",
      output: null,
      visit: null,
      visit_index: null,
      path: null,
      reason,
    }));
}

/**
 * The kinds of the given deliverables, one tab each: in the order of each
 * kind's first entry in the project's list, then the others in the order
 * they come.
 */
export function tabKinds(project: Project, items: Deliverable[]): string[] {
  const declared = [
    ...new Set((project.deliverables ?? []).map((d) => d.kind)),
  ];
  const kinds = new Set(items.map((d) => d.kind));
  return [
    ...declared.filter((k) => kinds.has(k)),
    ...[...kinds].filter((k) => !declared.includes(k)),
  ];
}

/**
 * What the Deliverables card says of an attempt whose last run did not
 * finish (isEnding): which run and how it ended, and that the deliverables
 * are what the runs before it made, or, for a record that keeps none, as an
 * older dataforge wrote it, that it keeps none; null for any other attempt.
 */
export function unfinished(
  project: Project,
  record: AttemptRecord,
): string | null {
  const last = record.visits.at(-1);
  if (last === undefined || !isEnding(last.outcome)) return null;
  const run = `${stateTitle(project, last.state)} · run ${record.visits.length.toString()}`;
  const how = statusOf(project, last.outcome).label;
  const kept =
    record.deliverables?.length === 0 && (project.deliverables ?? []).length > 0
      ? "Its record, from an older dataforge, keeps no deliverables."
      : "The deliverables are what the runs before it made.";
  return `${run} did not finish (${how}). ${kept}`;
}
