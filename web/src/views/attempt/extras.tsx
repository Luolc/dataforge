// What the heading of an attempt's page shows from the project's listing: the
// attempt's tags and a menu of its item's attempts. A listing that cannot be
// read, or has no row for the attempt, is said, not drawn as nothing.
import { Tooltip } from "radix-ui";

import type { Project } from "../../api/types";
import { toneText } from "../../ui/status";
import { useListing } from "./listing";
import { AttemptMenu } from "./menu";
import { AttemptTags } from "./tags";

export function HeaderExtras({
  project,
  attempt,
}: {
  project: Project;
  attempt: string;
}) {
  const rows = useListing(project, attempt);
  if (rows.state === "loading") return null;
  if (rows.state === "error")
    return (
      <Unread>{`The tags and the other attempts cannot be read: ${rows.message}`}</Unread>
    );
  const row = rows.data.find((r) => r.id === attempt);
  if (row === undefined)
    return (
      <Unread>
        The listing has no row for this attempt, so it has no tags and no other
        attempts.
      </Unread>
    );
  return (
    <Tooltip.Provider delayDuration={200}>
      <AttemptTags project={project} row={row} />
      <AttemptMenu project={project} rows={rows.data} current={row} />
    </Tooltip.Provider>
  );
}

function Unread({ children }: { children: string }) {
  return <span className={`text-xs ${toneText.bad}`}>{children}</span>;
}
