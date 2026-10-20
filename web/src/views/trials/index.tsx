import { DeliverableCard } from "../DeliverableCard";
import type { DeliverableProps } from "../kind";

/** The trials view, for a deliverable of kind trials; not built yet. */
export default function TrialsView({ deliverable }: DeliverableProps) {
  return (
    <DeliverableCard
      item={deliverable}
      note="The trials view is not built yet."
    />
  );
}
