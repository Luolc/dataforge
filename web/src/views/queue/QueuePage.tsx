// The Queue page: each queue, and its jobs that run, wait and lately ended,
// read on load, on each refresh, and every POLL_MS while a job is open
// (design-viz.md section 5).
import { RefreshCw, X } from "lucide-react";
import { use, useCallback, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { ApiError, getJson, postJson, urls } from "../../api/client";
import { usePolling } from "../../api/poll";
import {
  type Job,
  type Project,
  queueRead,
  type QueueRead,
  written,
} from "../../api/types";
import { Frame } from "../../shell/Shell";
import { ProjectsContext } from "../../shell/projects";
import { Note } from "../../ui/card";
import { useAsk } from "../../ui/ask";
import { control, Question } from "../../ui/confirm";
import { duration, epoch, time } from "../../ui/format";
import { Elapsed } from "../../ui/running";
import { Group } from "../home/HomeTable";
import { toneGlyph, toneText } from "../../ui/status";
import { ToneLabel } from "../../ui/status-mark";
import { isOpen, jobLabel } from "./status";

/** What a read gives, kept on the page while the next read loads. */
interface Read {
  data: QueueRead;
  /** When it was read, as ISO 8601. */
  at: string;
}

function useQueue() {
  const [generation, setGeneration] = useState({ n: 0, polled: false });
  const [read, setRead] = useState<Read>();
  const [done, setDone] = useState<{
    n: number;
    error: string | null;
    missing: boolean;
  }>();
  useEffect(() => {
    const controller = new AbortController();
    const n = generation.n;
    getJson(urls.queue(), queueRead, controller.signal).then(
      (data) => {
        setRead({ data, at: new Date().toISOString() });
        setDone({ n, error: null, missing: false });
      },
      (e: unknown) => {
        if (controller.signal.aborted) return;
        setDone({
          n,
          error: e instanceof Error ? e.message : String(e),
          missing: e instanceof ApiError && e.message.startsWith("404:"),
        });
      },
    );
    return () => {
      controller.abort();
    };
  }, [generation.n]);
  const refresh = useCallback(() => {
    setGeneration((g) => ({ n: g.n + 1, polled: false }));
  }, []);
  usePolling(read?.data.jobs.some(isOpen) ?? false, () => {
    setGeneration((g) => ({ n: g.n + 1, polled: true }));
  });
  const current = done?.n === generation.n ? done : null;
  return {
    read,
    error: current?.error ?? null,
    missing: current?.missing ?? false,
    reading: current === null && !generation.polled,
    refresh,
  };
}

export function QueuePage() {
  const { read, error, missing, reading, refresh } = useQueue();
  let body;
  if (missing) body = <Note>This site names no queue.</Note>;
  else if (read === undefined)
    body = (
      <Note>
        {error !== null ? `The queue cannot be read: ${error}` : "Loading…"}
      </Note>
    );
  else
    body = (
      <Queues read={read} reading={reading} error={error} onRefresh={refresh} />
    );
  return <Frame crumbs={[{ label: "Queue" }]}>{body}</Frame>;
}

const GROUPS = [
  { title: "Running", has: (j: Job) => j.state === "running" },
  { title: "Queued", has: (j: Job) => j.state === "queued" },
  { title: "Ended", has: (j: Job) => !isOpen(j) },
];

const HEADS: { title: string; numeric?: boolean }[] = [
  { title: "Status" },
  { title: "Job", numeric: true },
  { title: "Queue" },
  { title: "Item" },
  { title: "Action" },
  { title: "Queued" },
  { title: "Started" },
  { title: "Ended" },
  { title: "Duration", numeric: true },
  { title: "Exit code", numeric: true },
];

function Queues({
  read,
  reading,
  error,
  onRefresh,
}: {
  read: Read;
  reading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const { data } = read;
  const projects = use(ProjectsContext);
  const known = projects.state === "ok" ? projects.data : [];
  const running = data.queues.reduce((n, q) => n + q.running, 0);
  const ended = data.jobs.filter((j) => !isOpen(j)).length;
  const baseId = useId();
  const [said, setSaid] = useState<string | null>(null);
  const cancel = async (job: Job) => {
    try {
      await postJson(urls.cancel(job.id), written);
      setSaid(null);
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e);
      setSaid(`Job ${job.id.toString()} was not canceled (${message}).`);
    }
    onRefresh();
  };
  const Bad = toneGlyph.bad;
  return (
    <div className="flex flex-col gap-3 px-6 py-5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <ul
          aria-label="Queues"
          className="flex flex-wrap gap-x-5 gap-y-1 text-xs"
        >
          {data.queues.map((q) => (
            <li key={q.name}>
              <span className="font-mono font-medium">{q.name}</span>{" "}
              <span className="text-fg-2 tabular-nums">
                {`${q.running.toString()} of ${q.limit.toString()} running · ${q.queued.toString()} queued`}
              </span>
            </li>
          ))}
          <li className="text-fg-2 tabular-nums">
            {`The machine: ${running.toString()} of ${data.max_running.toString()} running`}
          </li>
        </ul>
        <div className="ml-auto flex items-center gap-2 text-xs text-fg-3">
          {error !== null && <span className="text-bad">{error}</span>}
          <span aria-live="polite">
            {reading ? "Reading…" : `Read ${time(read.at)}`}
          </span>
          <button type="button" onClick={onRefresh} className={control}>
            <RefreshCw className="size-3.5 text-icon" aria-hidden />
            Refresh
          </button>
        </div>
      </div>
      <div role="status" className="text-xs">
        {said !== null && (
          <span className={`inline-flex items-center gap-1.5 ${toneText.bad}`}>
            <Bad className="size-3.5 flex-none" aria-hidden />
            {said}
          </span>
        )}
      </div>
      {data.jobs.length === 0 ? (
        <p className="py-6 text-center text-fg-3">
          No job has been queued yet.
        </p>
      ) : (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line bg-head text-xs text-fg-2">
              {HEADS.map((h) => (
                <th
                  key={h.title}
                  scope="col"
                  className={`px-2 py-1 font-medium whitespace-nowrap ${h.numeric === true ? "text-right" : "text-left"}`}
                >
                  {h.title}
                </th>
              ))}
              <th scope="col">
                <span className="sr-only">Cancel</span>
              </th>
            </tr>
          </thead>
          {GROUPS.map(({ title, has }) => {
            const jobs = data.jobs.filter(has);
            if (jobs.length === 0) return null;
            const n = jobs.length;
            const k = jobs.filter((j) => {
              const { tone } = jobLabel(j);
              return tone === "bad" || tone === "warn";
            }).length;
            return (
              <Group
                key={title}
                id={`${baseId}-${title}`}
                title={title}
                summary={
                  k > 0
                    ? `${k.toString()} of ${n.toString()} bad or warn`
                    : n.toString()
                }
                width={HEADS.length + 1}
              >
                {jobs.map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    projects={known}
                    onCancel={cancel}
                  />
                ))}
              </Group>
            );
          })}
        </table>
      )}
      {ended >= data.ended_limit && (
        <p className="text-xs text-fg-3">
          {`Only the last ${data.ended_limit.toString()} jobs that ended are shown.`}
        </p>
      )}
    </div>
  );
}

const cell = "px-2 py-1.5 align-top";
const numeric = `${cell} text-right whitespace-nowrap tabular-nums`;
const LABELED = ["project", "action", "item"];

function JobRow({
  job,
  projects,
  onCancel,
}: {
  job: Job;
  projects: Project[];
  onCancel: (job: Job) => Promise<void>;
}) {
  const project = projects.find((p) => p.id === job.labels["project"]);
  const item = job.labels["item"];
  const action = job.labels["action"];
  const label =
    project?.actions?.find((a) => a.name === action)?.label ?? action;
  const others = Object.entries(job.labels).filter(
    ([k]) => !LABELED.includes(k),
  );
  const cancelable = isOpen(job) && job.cancel_requested_at === null;
  const id = job.id.toString();
  const ask = useAsk();
  const questionId = useId();
  return (
    <>
      <tr className="border-b border-line-subtle hover:bg-hover">
        <td className={`${cell} whitespace-nowrap`}>
          <ToneLabel {...jobLabel(job)} />
        </td>
        <td className={numeric}>{id}</td>
        <td className={`${cell} font-mono`}>{job.queue}</td>
        <td className={`${cell} font-mono whitespace-nowrap`}>
          {item === undefined ? (
            "—"
          ) : project !== undefined ? (
            <Link
              to={`/${encodeURIComponent(project.id)}?${new URLSearchParams({ q: item }).toString()}`}
              className="link hover:underline"
            >
              {item}
            </Link>
          ) : (
            item
          )}
        </td>
        <td className={cell}>
          {label === undefined && others.length === 0 && "—"}
          {label !== undefined && (
            <div className="whitespace-nowrap">{label}</div>
          )}
          {others.length > 0 && (
            <div className="font-mono text-xs text-fg-2">
              {others.map(([k, v]) => `${k}=${v}`).join(" ")}
            </div>
          )}
        </td>
        <td className={`${cell} whitespace-nowrap`}>{epoch(job.queued_at)}</td>
        <td className={`${cell} whitespace-nowrap`}>
          {job.started_at === null ? "—" : epoch(job.started_at)}
        </td>
        <td className={`${cell} whitespace-nowrap`}>
          {job.ended_at === null ? "—" : epoch(job.ended_at)}
        </td>
        <td className={numeric}>
          <Took job={job} />
        </td>
        <td className={numeric}>
          {job.exit_code === null ? "—" : job.exit_code.toString()}
        </td>
        <td className={`${cell} text-right`}>
          {cancelable && (
            <button
              type="button"
              className={control}
              aria-expanded={ask.asking}
              aria-controls={questionId}
              onClick={ask.ask}
            >
              <X className="size-3.5 text-icon" aria-hidden />
              Cancel
            </button>
          )}
        </td>
      </tr>
      {cancelable && ask.asking && (
        <tr id={questionId} className="border-b border-line-subtle">
          <td colSpan={HEADS.length + 1} className={`${cell} text-right`}>
            <Question
              ask={ask}
              question={
                job.state === "running"
                  ? `Cancel job ${id}? Its processes are sent SIGTERM.`
                  : `Cancel job ${id}?`
              }
              yes="Cancel job"
              onYes={() => onCancel(job)}
            />
          </td>
        </tr>
      )}
    </>
  );
}

/** How long a job ran, going on while it runs; "—" before it starts. */
function Took({ job }: { job: Job }) {
  if (job.started_at === null) return "—";
  if (job.ended_at !== null) return duration(job.ended_at - job.started_at);
  return <Elapsed since={new Date(job.started_at * 1000).toISOString()} />;
}
