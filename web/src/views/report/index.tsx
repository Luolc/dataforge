import { DeliverableCard } from "../DeliverableCard";
import type { DeliverableProps } from "../kind";

/** The report view, for a deliverable of kind report; not built yet. */
export default function ReportView({ deliverable }: DeliverableProps) {
  return (
    <DeliverableCard
      item={deliverable}
      note="The report view is not built yet."
    />
  );
}
