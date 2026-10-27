// A tab's versions as its column shows them: grouped by the patterns they
// came from, a version of a pattern that lies under another's under the
// version it lies in, and named by their labels split at their last " › ".
import type { ProjectTab, TabVersion } from "../../api/types";
import { readValue } from "../../ui/format";

/** What divides a label's group heading from its entry. */
const STEP = " › ";

/** A version as an entry of the column, with those that lie in it. */
interface Entry {
  version: TabVersion;
  /** What its entry says. */
  title: string;
  /** How deep it lies under its group's first entry. */
  depth: number;
  /** The versions that lie in it, in the order the column lists them. */
  under: Entry[];
}

/** A group of the column, with its heading, null for none. */
export interface Group {
  key: string;
  heading: string | null;
  /** Each entry followed by those that lie in it, depth first. */
  entries: Entry[];
}

const components = (path: string) => path.split("/");

/**
 * For each of a tab's patterns, the index of the one it lies under: the
 * deepest whose components lead its own; -1 for none.
 */
function nesting(tab: ProjectTab): number[] {
  const patterns = tab.versions.map((s) => components(s.pattern));
  return patterns.map((own, i) => {
    let found = -1;
    patterns.forEach((other, j) => {
      if (
        j !== i &&
        other.length < own.length &&
        other.every((c, k) => c === own[k]) &&
        (found === -1 || other.length > (patterns[found]?.length ?? 0))
      )
        found = j;
    });
    return found;
  });
}

/** A version's value named by its pattern's version_title; null for none. */
function valueOf(version: TabVersion, tab: ProjectTab): string | null {
  const format = tab.versions[version.source]?.version_title?.format;
  return format !== undefined &&
    version.title?.read === "found" &&
    version.title.value !== null
    ? readValue("found", version.title.value, { format })
    : null;
}

/** A label cut at its last STEP: what comes before it, null without one, and after. */
function split(label: string | null): [string | null, string | null] {
  if (label === null) return [null, null];
  const at = label.lastIndexOf(STEP);
  return at < 0
    ? [null, label]
    : [label.slice(0, at), label.slice(at + STEP.length)];
}

/**
 * What a version is titled by where it is shown: its label, then its value
 * or, where that is not read, its directory; its label alone for a pattern
 * with no version_title; and with no label, its value, else its directory.
 */
export function versionTitle(version: TabVersion, tab: ProjectTab): string {
  const label = version.label ?? null;
  const value = valueOf(version, tab);
  if (label === null) return value ?? version.dir;
  if (tab.versions[version.source]?.version_title == null) return label;
  return `${label} · ${value ?? version.dir}`;
}

/**
 * What a version's entry says: what its label has after its last STEP, then
 * its value; with no STEP in its label, its value, else its directory.
 */
function entryTitle(version: TabVersion, tab: ProjectTab): string {
  const [before, after] = split(version.label ?? null);
  const value = valueOf(version, tab);
  if (before === null || after === null || after === "")
    return value ?? version.dir;
  return value === null ? after : `${after} · ${value}`;
}

/** A group's heading from its first version: its label before its last STEP, else its label. */
function headingOf(version: TabVersion): string | null {
  const [before, after] = split(version.label ?? null);
  return before ?? after;
}

/**
 * The column's groups. A version of a pattern that lies under another lies
 * under the version whose directory leads its own, after it, those of one
 * version in the reverse of the tab's order (review-1 before review-2); one
 * that lies in no version is as one of no pattern's. Each version of a
 * pattern that others lie under heads a group of its own, and the versions of
 * every other pattern make one group; the groups come in the order of their
 * first versions.
 */
export function versionGroups(
  versions: TabVersion[],
  tab: ProjectTab,
): Group[] {
  const parentOf = nesting(tab);
  const byDir = new Map(versions.map((v) => [v.dir, v]));
  const parent = (v: TabVersion): TabVersion | undefined => {
    const over = parentOf[v.source] ?? -1;
    const pattern = tab.versions[over]?.pattern;
    if (pattern === undefined) return undefined;
    const dir = components(v.dir)
      .slice(0, components(pattern).length)
      .join("/");
    return byDir.get(dir);
  };
  const children = new Map<TabVersion, TabVersion[]>();
  const top: TabVersion[] = [];
  for (const v of versions) {
    const p = parent(v);
    if (p === undefined) top.push(v);
    else children.set(p, [...(children.get(p) ?? []), v]);
  }
  const heads = new Set(parentOf.filter((p) => p >= 0));
  const entry = (v: TabVersion, depth: number): Entry => ({
    version: v,
    title: entryTitle(v, tab),
    depth,
    under: (children.get(v) ?? []).toReversed().map((c) => entry(c, depth + 1)),
  });
  const flat = (e: Entry): Entry[] => [e, ...e.under.flatMap(flat)];
  const groups: Group[] = [];
  const ofSource = new Map<number, Group>();
  for (const v of top) {
    if (heads.has(v.source)) {
      groups.push({
        key: v.dir,
        heading: headingOf(v) ?? versionTitle(v, tab),
        entries: flat(entry(v, 0)),
      });
      continue;
    }
    let group = ofSource.get(v.source);
    if (group === undefined) {
      group = {
        key: `source-${v.source.toString()}`,
        heading: headingOf(v),
        entries: [],
      };
      ofSource.set(v.source, group);
      groups.push(group);
    }
    group.entries.push(...flat(entry(v, 0)));
  }
  return groups;
}

/** The entry of a version among the groups; undefined for none. */
export function entryOf(groups: Group[], version: TabVersion) {
  return groups.flatMap((g) => g.entries).find((e) => e.version === version);
}
