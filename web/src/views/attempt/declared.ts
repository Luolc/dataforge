// The deliverables a project declares, set beside a record's (design-viz.md
// section 5, "Attempt page"): a record has none until its attempt ends.
import type { AttemptRecord, Deliverable, Project } from "../../api/types";
import { attemptStatus } from "../../ui/status";

/**
 * The record with each deliverable titled as the project titles the one of its
 * name; a deliverable the project does not list keeps the title it recorded.
 */
export function retitled(
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
    })),
  };
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
      ? "This attempt has not reached the state that makes it."
      : "This attempt ended without it.";
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
