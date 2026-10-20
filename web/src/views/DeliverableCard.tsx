import type { Deliverable } from "../api/types";

/** A deliverable's name, kind, path and reason, as the record has them. */
export function DeliverableCard({
  item,
  note,
}: {
  item: Deliverable;
  note?: string;
}) {
  return (
    <section className="rounded-panel border border-line p-4">
      <h3 className="font-semibold">{item.title}</h3>
      {note !== undefined && <p className="mt-1 text-fg-3">{note}</p>}
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-fg-2">
        <dt className="text-fg-3">Name</dt>
        <dd className="font-mono text-xs">{item.name}</dd>
        <dt className="text-fg-3">Kind</dt>
        <dd className="font-mono text-xs">{item.kind}</dd>
        <dt className="text-fg-3">Path</dt>
        <dd className="font-mono text-xs">{item.path ?? "—"}</dd>
        {item.reason !== null && (
          <>
            <dt className="text-fg-3">Reason</dt>
            <dd>{item.reason}</dd>
          </>
        )}
      </dl>
    </section>
  );
}
