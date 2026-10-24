// The home page's view a reader sets, kept in localStorage per project as
// {"version": 1, "hidden": [<column key>, ...], "rules": [<rule>, ...]}:
// which columns it hides and the filter's rules. Each part is saved with the
// rest of the view as it is kept. The rules are in the page's URL too, as
// f.<field>=<op>:<value>,<value>, each value URL-encoded, so that a link
// carries them.
import type { Column } from "../../api/types";
import type { Rule } from "../../ui/rule";

/** Where a project's view is kept in localStorage. */
export const viewKey = (project: string) => `viz.home.${project}`;

/** The key of the status field, as a rule names it. */
export const STATUS_FIELD = "status";

/** The key of a project column in the view: by its title. */
export const columnKey = (title: string) => `col.${title}`;

const VERSION = 1;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** What is kept for the project, when it is a view of this version; else null. */
function kept(project: string): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(viewKey(project));
    if (raw === null) return null;
    const view: unknown = JSON.parse(raw);
    return isObject(view) && view["version"] === VERSION ? view : null;
  } catch {
    return null;
  }
}

/**
 * The columns kept hidden for the project, of those keys names, in their
 * order: a column its project file no longer has is dropped.
 */
export function loadHidden(project: string, keys: readonly string[]): string[] {
  const hidden = kept(project)?.["hidden"];
  return Array.isArray(hidden) ? keys.filter((k) => hidden.includes(k)) : [];
}

/** Keeps a part of the project's view, and the rest of it as it is kept. */
function save(project: string, part: Record<string, unknown>) {
  try {
    localStorage.setItem(
      viewKey(project),
      JSON.stringify({ ...kept(project), version: VERSION, ...part }),
    );
  } catch {
    // Private mode: the view lasts for this page only.
  }
}

/** Keeps the columns hidden for the project, and the rest of its view. */
export function saveHidden(project: string, hidden: readonly string[]) {
  save(project, { hidden });
}

/** The keys of the fields a project's items can be filtered by. */
export const filterFields = (columns: readonly Column[]): string[] => [
  STATUS_FIELD,
  ...columns.flatMap((c) => (c.filter === true ? [columnKey(c.title)] : [])),
];

/**
 * The rules among given that hold: one per field, the first, of a field of
 * fields, with an op of any or none and at least one value, each value once.
 */
function holding(given: readonly unknown[], fields: readonly string[]): Rule[] {
  const rules: Rule[] = [];
  for (const r of given) {
    if (!isObject(r)) continue;
    const { field, op, values } = r;
    if (
      typeof field !== "string" ||
      !fields.includes(field) ||
      rules.some((x) => x.field === field) ||
      (op !== "any" && op !== "none") ||
      !Array.isArray(values)
    )
      continue;
    const kept = [
      ...new Set(values.filter((v): v is string => typeof v === "string")),
    ];
    if (kept.length > 0) rules.push({ field, op, values: kept });
  }
  return rules;
}

/**
 * The rules kept for the project, of fields it still filters by; a view
 * with no rules has none.
 */
export function loadRules(project: string, fields: readonly string[]): Rule[] {
  const rules = kept(project)?.["rules"];
  return Array.isArray(rules) ? holding(rules, fields) : [];
}

/** Keeps the project's rules, and the rest of its view. */
export function saveRules(project: string, rules: readonly Rule[]) {
  save(project, { rules });
}

const PREFIX = "f.";

/** The older key of a column's filter, by the column's index. */
const OLDER_COLUMN = /^column-(\d+)$/;

/** A part of a URL's query decoded, + as a space; null when it is not well formed. */
function decoded(part: string): string | null {
  try {
    return decodeURIComponent(part.replace(/\+/g, " "));
  } catch {
    return null;
  }
}

/** A URL's query: its rules, those of the older kind too, and its other keys. */
export interface Query {
  /** The rules that hold, of fields the project filters by. */
  rules: Rule[];
  /** Its keys but those of rules. */
  others: URLSearchParams;
}

/**
 * Reads a URL's query (location.search): its f.<field> keys, and the keys of
 * the older kind, ?status=<word> and ?column-<index>=<value>, a column by its
 * place among columns, as the rules they stand for; a rule of a field the
 * project does not filter by, or with an op other than any or none, is
 * dropped. A field with both kinds takes its f. key.
 */
export function readQuery(search: string, columns: readonly Column[]): Query {
  const fields = filterFields(columns);
  const older: unknown[] = [];
  const given: unknown[] = [];
  const others = new URLSearchParams();
  for (const part of search.replace(/^\?/, "").split("&")) {
    if (part === "") continue;
    const at = part.indexOf("=");
    const name = decoded(at === -1 ? part : part.slice(0, at));
    const raw = at === -1 ? "" : part.slice(at + 1);
    if (name === null) continue;
    if (name.startsWith(PREFIX)) {
      // The value is not decoded whole: a comma in a value is encoded,
      // and only a bare one parts two values.
      const colon = raw.indexOf(":");
      if (colon !== -1)
        given.push({
          field: name.slice(PREFIX.length),
          op: decoded(raw.slice(0, colon)),
          values: raw
            .slice(colon + 1)
            .split(",")
            .map(decoded),
        });
      continue;
    }
    const value = decoded(raw);
    if (value === null) continue;
    const index = OLDER_COLUMN.exec(name)?.[1];
    if (name === STATUS_FIELD || index !== undefined) {
      const column = index === undefined ? undefined : columns[Number(index)];
      older.push({
        // A column that is not there gives no field, and its rule is dropped.
        field:
          index === undefined
            ? STATUS_FIELD
            : column && columnKey(column.title),
        op: "any",
        values: [value],
      });
      continue;
    }
    others.append(name, value);
  }
  const rules = holding(given, fields);
  for (const r of holding(older, fields))
    if (!rules.some((x) => x.field === r.field)) rules.push(r);
  return { rules, others };
}

/** A URL's query (with its ?, or "" for none) of others and the rules. */
export function writeQuery(
  others: URLSearchParams,
  rules: readonly Rule[],
): string {
  const parts = [
    ...(others.toString() === "" ? [] : [others.toString()]),
    ...rules.map(
      (r) =>
        `${PREFIX}${encodeURIComponent(r.field)}=${r.op}:${r.values.map(encodeURIComponent).join(",")}`,
    ),
  ];
  return parts.length === 0 ? "" : `?${parts.join("&")}`;
}
