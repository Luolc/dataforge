import type { Deliverable } from "../api/types";

/** A deliverable's name, kind, path and reason, as the record has them. */
export function DeliverableCard({ item }: { item: Deliverable }) {
  return (
    <section className="rounded-panel border border-line p-4">
      <h3 className="font-semibold">{item.title}</h3>
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

/** Where a view that is not built yet goes: its deliverables as cards. */
export function Placeholder({
  what,
  items,
}: {
  what: string;
  items: Deliverable[];
}) {
  return (
    <div className="flex flex-col gap-3 p-7">
      <p className="text-fg-3">The {what} is not built yet.</p>
      {items.map((item) => (
        <DeliverableCard key={item.name} item={item} />
      ))}
    </div>
  );
}
