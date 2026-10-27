/**
 * The URL of an attempt's page, with its tab, and its run or discarded visit,
 * the version and the part of a project's tab, the deliverable and the trial
 * shown, or the file shown, when given.
 */
export function attemptHref(
  project: string,
  attempt: string,
  at: {
    tab?: string;
    run?: number;
    discarded?: number;
    version?: string;
    part?: string;
    deliverable?: string;
    trial?: string;
    file?: string;
  } = {},
): string {
  const query = new URLSearchParams({ id: attempt });
  if (at.tab !== undefined) query.set("tab", at.tab);
  if (at.run !== undefined) query.set("run", String(at.run));
  if (at.discarded !== undefined) query.set("discarded", String(at.discarded));
  if (at.version !== undefined) query.set("version", at.version);
  if (at.part !== undefined) query.set("part", at.part);
  if (at.deliverable !== undefined) query.set("deliverable", at.deliverable);
  if (at.trial !== undefined) query.set("trial", at.trial);
  if (at.file !== undefined) query.set("file", at.file);
  return `/${encodeURIComponent(project)}/attempt?${query.toString()}`;
}
