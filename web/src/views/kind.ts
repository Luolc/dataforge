import type {
  AttemptRecord,
  Deliverable,
  LiveInfo,
  Project,
} from "../api/types";
import { NOT_RUN } from "../ui/status";

/** The project file's entry for a deliverable, by its name, if it has one. */
export function declared(
  project: Pick<Project, "deliverables">,
  name: string,
): NonNullable<Project["deliverables"]>[number] | undefined {
  return project.deliverables?.find((d) => d.name === name);
}

/** Not run, with why for a deliverable not reached, when there is a why. */
export function notRun(deliverable: Pick<Deliverable, "reason">): string {
  const { reason } = deliverable;
  return reason === null || reason === "" ? NOT_RUN : `${NOT_RUN}: ${reason}`;
}

/** What a deliverable kind's view gets: one deliverable and its record. */
export interface DeliverableProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  deliverable: Deliverable;
  /** Whether the attempt runs now; null or absent while it is not known. */
  live?: LiveInfo | null;
}

/**
 * Whether a deliverable the attempt reached leads its kind: the project's
 * condition for it holds on the outcome data of the visit it came from.
 */
function leads(
  project: Project,
  record: AttemptRecord,
  item: Deliverable,
): boolean {
  const when = declared(project, item.name)?.default;
  if (typeof when !== "object" || item.path === null) return false;
  const visit =
    item.visit_index === null ? undefined : record.visits[item.visit_index];
  let value: unknown = visit?.data;
  for (const key of when.field) {
    if (!isObject(value) || !Object.hasOwn(value, key)) return false;
    value = value[key];
  }
  return sameJson(value, when.equals);
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function sameJson(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b))
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item, i) => sameJson(item, b[i]))
    );
  if (isObject(a) && isObject(b)) {
    const keys = Object.keys(a);
    return (
      keys.length === Object.keys(b).length &&
      keys.every((key) => Object.hasOwn(b, key) && sameJson(a[key], b[key]))
    );
  }
  return a === b;
}

/**
 * Deliverables of a kind with those that lead it (leads) first, then the
 * others, each in their own order.
 */
export function leadersFirst(
  project: Project,
  record: AttemptRecord,
  items: Deliverable[],
): Deliverable[] {
  const first = (item: Deliverable) => leads(project, record, item);
  return [...items.filter(first), ...items.filter((item) => !first(item))];
}

/**
 * Deliverables of a kind in the order their tab stacks them: those that
 * lead it, then the others the attempt reached, then the rest, each in
 * their own order.
 */
export function reachedFirst(
  project: Project,
  record: AttemptRecord,
  items: Deliverable[],
): Deliverable[] {
  const reached = (item: Deliverable) => item.path !== null;
  return leadersFirst(project, record, [
    ...items.filter(reached),
    ...items.filter((item) => !reached(item)),
  ]);
}

/**
 * The deliverable of a kind that its tab is titled and counted by and opens
 * on: the first that leads it, else the first the attempt reached, else the
 * one the project marks default, else the first.
 */
export function leading(
  project: Project,
  record: AttemptRecord,
  items: Deliverable[],
): Deliverable | undefined {
  const reached = reachedFirst(project, record, items).find(
    (item) => item.path !== null,
  );
  if (reached !== undefined) return reached;
  const marked = (project.deliverables ?? []).filter((d) => d.default === true);
  return (
    items.find((item) => marked.some((d) => d.name === item.name)) ?? items[0]
  );
}

/** The title of a kind's tab: the project's for the kind, else its leading one's. */
export function kindTitle(
  project: Project,
  record: AttemptRecord,
  kind: string,
  items: Deliverable[],
): string {
  return (
    project.kinds?.[kind]?.title ??
    leading(project, record, items)?.title ??
    kind
  );
}
