/**
 * The URL of an attempt's page, with its tab, and its run or discarded visit,
 * the part of a project's tab, or the file shown, when given.
 */
export function attemptHref(
  project: string,
  attempt: string,
  at: {
    tab?: string;
    run?: number;
    discarded?: number;
    part?: string;
    file?: string;
  } = {},
): string {
  const query = new URLSearchParams({ id: attempt });
  if (at.tab !== undefined) query.set("tab", at.tab);
  if (at.run !== undefined) query.set("run", String(at.run));
  if (at.discarded !== undefined) query.set("discarded", String(at.discarded));
  if (at.part !== undefined) query.set("part", at.part);
  if (at.file !== undefined) query.set("file", at.file);
  return `/${encodeURIComponent(project)}/attempt?${query.toString()}`;
}
