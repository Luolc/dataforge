// A data item's actions, under the heading of one of its attempts: a button
// each, greyed with the reason when the backend says it may not be submitted
// now (design-viz.md section 5).
import { ListPlus } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import {
  getJson,
  nextGeneration,
  postJson,
  urls,
  useLoaded,
  useRefresh,
} from "../../api/client";
import { actionStates, type Project, written } from "../../api/types";
import { Confirm } from "../../ui/confirm";
import { toneGlyph, toneText } from "../../ui/status";

// The item an attempt ID names: its first item.depth components.
const itemOf = (project: Project, attempt: string) =>
  attempt.split("/").slice(0, project.item.depth).join("/");

type Said = { ok: true; text: string } | { ok: false; text: string };

export function ItemActions({
  project,
  attempt,
}: {
  project: Project;
  attempt: string;
}) {
  const item = itemOf(project, attempt);
  const url = urls.actions(project.id, item);
  // Read with the page, and again after each write: generations only grow, so
  // the later of the two changes whenever either does.
  const [wrote, setWrote] = useState(0);
  const refresh = Math.max(useRefresh(), wrote);
  const states = useLoaded(
    url,
    (signal) => getJson(url, actionStates, signal),
    refresh,
  );
  const [said, setSaid] = useState<Said | null>(null);
  const baseId = useId();
  const submit = async (name: string, label: string) => {
    try {
      const { job } = await postJson(
        urls.submit(project.id, name, item),
        written,
      );
      setSaid({
        ok: true,
        text: `${label}: queued as job ${job.id.toString()} in ${job.queue}.`,
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      setSaid({ ok: false, text: `${label} was not queued (${message}).` });
    }
    setWrote(nextGeneration());
  };
  const Bad = toneGlyph.bad;
  return (
    <section
      aria-label={`Actions of ${project.item.title} ${item}`}
      className="flex flex-col gap-1.5 px-7 pt-3"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-xs text-fg-3">
          {project.item.title} <span className="font-mono">{item}</span>
        </span>
        {states.state === "error" && (
          <span className={`text-xs ${toneText.bad}`}>
            The actions cannot be read: {states.message}
          </span>
        )}
        {states.state === "ok" &&
          states.data.map((a) => {
            const reasonId = `${baseId}-${a.name}`;
            return (
              <span key={a.name} className="inline-flex items-center gap-2">
                <Confirm
                  question={`Queue ${a.label} for ${item} in ${a.queue}?`}
                  yes="Queue"
                  onYes={() => submit(a.name, a.label)}
                  disabled={a.reason !== null}
                  {...(a.reason !== null ? { describedBy: reasonId } : {})}
                >
                  <ListPlus className="size-3.5 text-icon" aria-hidden />
                  {a.label}
                </Confirm>
                {a.reason !== null && (
                  <span id={reasonId} className="text-xs text-fg-3">
                    {a.reason}
                  </span>
                )}
              </span>
            );
          })}
      </div>
      <div role="status" className="text-xs">
        {said !== null &&
          (said.ok ? (
            <span>
              {said.text}{" "}
              <Link to="/queue" className="link hover:underline">
                Open the queue
              </Link>
            </span>
          ) : (
            <span
              className={`inline-flex items-center gap-1.5 ${toneText.bad}`}
            >
              <Bad className="size-3.5 flex-none" aria-hidden />
              {said.text}
            </span>
          ))}
      </div>
    </section>
  );
}
