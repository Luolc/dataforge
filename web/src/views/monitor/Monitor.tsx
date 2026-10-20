// The Monitor page: the machine's and a batch's curves and the containers'
// time bars, from a project's resource monitor database, read on load and on
// each refresh, never on a timer.
import { RefreshCw, RotateCcw, X } from "lucide-react";
import { use, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import type { z } from "zod";

import { ApiError, getJson } from "../../api/client";
import type { Project } from "../../api/types";
import { Frame } from "../../shell/Shell";
import { ProjectsContext } from "../../shell/projects";
import { Card, Note } from "../../ui/card";
import { bytes, cores, duration, epoch, time } from "../../ui/format";
import { Bars } from "./Bars";
import { barLabel } from "./containers";
import { Chart, type Series, Swatch } from "./Chart";
import {
  curve,
  type Curve,
  monitorUrls,
  noDatabase,
  type Run,
  runs,
  type Selected,
  window as windowSchema,
  type Window,
} from "./types";

/** The Monitor page, loaded with uPlot only when it is opened. */
export default function Monitor() {
  const projects = use(ProjectsContext);
  const [params, setParams] = useSearchParams();
  let body;
  if (projects.state !== "ok") {
    body = (
      <Note>{projects.state === "error" ? projects.message : "Loading…"}</Note>
    );
  } else {
    const watched = projects.data.filter((p) => p.monitor === true);
    const chosen =
      watched.find((p) => p.id === params.get("project")) ?? watched[0];
    body =
      chosen === undefined ? (
        <Note>No project of this site names a monitor database.</Note>
      ) : (
        <MonitorOf
          key={chosen.id}
          project={chosen}
          projects={watched}
          onProject={(id) => {
            setParams({ project: id });
          }}
        />
      );
  }
  return <Frame crumbs={[{ label: "Monitor" }]}>{body}</Frame>;
}

/** What a read gives, kept on the page while the next read loads. */
function useRead<T>(
  url: string | null,
  schema: z.ZodType<T>,
  generation: number,
) {
  const key = JSON.stringify([url, generation]);
  const [kept, setKept] = useState<{ url: string; data: T; at: string }>();
  const [done, setDone] = useState<{
    key: string;
    error: string | null;
    missing: string | null;
  }>();
  useEffect(() => {
    if (url === null) return;
    const controller = new AbortController();
    getJson(url, schema, controller.signal).then(
      (data) => {
        setKept({ url, data, at: new Date().toISOString() });
        setDone({ key, error: null, missing: null });
      },
      (e: unknown) => {
        if (controller.signal.aborted) return;
        const missing =
          e instanceof ApiError ? noDatabase.safeParse(e.body) : null;
        setDone({
          key,
          error: e instanceof Error ? e.message : String(e),
          missing: missing?.success === true ? missing.data.db : null,
        });
      },
    );
    return () => {
      controller.abort();
    };
    // schema is a constant; key names the url and the read.
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [key]);
  return {
    kept: url !== null && kept?.url === url ? kept : null,
    error: done?.key === key ? done.error : null,
    /** The database as the project file names it, when it does not exist. */
    missing: done?.key === key ? done.missing : null,
    reading: url !== null && done?.key !== key,
  };
}

const number = (v: string | null) => {
  if (v === null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover";

function MonitorOf({
  project,
  projects,
  onProject,
}: {
  project: Project;
  projects: Project[];
  onProject: (id: string) => void;
}) {
  const [params, setParams] = useSearchParams();
  const [generation, setGeneration] = useState(0);
  const update = useCallback(
    (change: (p: URLSearchParams) => void) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        change(next);
        return next;
      });
    },
    [setParams],
  );

  const listed = useRead(monitorUrls.runs(project.id), runs, generation);
  const all = listed.kept?.data ?? [];
  const asked = number(params.get("run"));
  const run = all.find((r) => r.run_id === asked) ?? all.at(-1);
  const from = number(params.get("from"));
  const to = number(params.get("to"));
  const shown = useRead(
    run === undefined
      ? null
      : monitorUrls.window(project.id, run.run_id, from, to),
    windowSchema,
    generation,
  );
  const w = shown.kept?.data ?? null;
  const kind = params.get("container") !== null ? "container" : "attempt";
  const id = params.get(kind);
  const selected: Selected | null = id === null ? null : { kind, id };
  const picked = useRead(
    w === null || selected === null
      ? null
      : monitorUrls.curve(project.id, w.run_id, w.t0, w.t1, selected),
    curve,
    generation,
  );

  const zoom = useCallback(
    (a: number, b: number) => {
      update((p) => {
        p.set("from", a.toFixed(3));
        p.set("to", b.toFixed(3));
      });
    },
    [update],
  );
  const select = (s: Selected | null) => {
    update((p) => {
      p.delete("attempt");
      p.delete("container");
      if (s !== null) p.set(s.kind, s.id);
    });
  };

  const reading = listed.reading || shown.reading || picked.reading;
  const error = listed.error ?? shown.error ?? picked.error;
  const read = shown.kept?.at ?? listed.kept?.at;

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center gap-2">
        {projects.length > 1 && (
          <label className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs focus-within:border-fg-3">
            <span className="text-fg-3">Project</span>
            <select
              value={project.id}
              onChange={(e) => {
                onProject(e.target.value);
              }}
              className="h-6 bg-transparent font-medium text-fg outline-none"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {all.length > 0 && run !== undefined && (
          <label className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs focus-within:border-fg-3">
            <span className="text-fg-3">Run</span>
            <select
              value={run.run_id}
              onChange={(e) => {
                update((p) => {
                  p.set("run", e.target.value);
                  for (const k of ["from", "to", "attempt", "container"])
                    p.delete(k);
                });
              }}
              className="h-6 bg-transparent font-medium text-fg outline-none"
            >
              {[...all].reverse().map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {runLabel(r)}
                </option>
              ))}
            </select>
          </label>
        )}
        {(from !== null || to !== null) && (
          <button
            type="button"
            className={control}
            onClick={() => {
              update((p) => {
                p.delete("from");
                p.delete("to");
              });
            }}
          >
            <RotateCcw className="size-3.5 text-icon" aria-hidden />
            Whole run
          </button>
        )}
        <div className="ml-auto flex items-center gap-2 text-xs text-fg-3">
          {error !== null && listed.kept !== null && (
            <span className="text-bad">{error}</span>
          )}
          <span aria-live="polite">
            {reading
              ? "Reading…"
              : read !== undefined
                ? `Read ${time(read)}`
                : ""}
          </span>
          <button
            type="button"
            onClick={() => {
              setGeneration((g) => g + 1);
            }}
            className={control}
          >
            <RefreshCw className="size-3.5 text-icon" aria-hidden />
            Refresh
          </button>
        </div>
      </div>
      {listed.kept === null && listed.missing !== null ? (
        <NoDatabase db={listed.missing} />
      ) : listed.kept === null ? (
        <Note>
          {listed.error !== null
            ? `The monitor cannot be read: ${listed.error}`
            : "Loading…"}
        </Note>
      ) : all.length === 0 ? (
        <Note>The monitor database has no runs yet.</Note>
      ) : w === null ? (
        <Note>{shown.error ?? "Loading…"}</Note>
      ) : (
        <Panels
          project={project}
          w={w}
          selected={selected}
          picked={picked.kept?.data ?? null}
          onZoom={zoom}
          onSelect={select}
        />
      )}
    </div>
  );
}

/** Where the page stands before any sampler has written the database. */
function NoDatabase({ db }: { db: string }) {
  return (
    <div className="flex flex-col gap-2 p-6 text-fg-3">
      <p className="font-medium text-fg">
        No sampler has written this monitor database yet.
      </p>
      <p>
        The project file names it{" "}
        <code className="font-mono text-xs break-all text-fg">{db}</code>, and
        there is no file there.
      </p>
      <p>
        To fill this page, start a resource sampler that writes to that
        database. Once it has written its first run, Refresh reads it.
      </p>
    </div>
  );
}

/** A run in the run menu: when it started, how long, and what it was. */
function runLabel(r: Run): string {
  const length =
    r.ended_at === null ? "no end" : duration(r.ended_at - r.started_at);
  return `${epoch(r.started_at)} · ${length} · ${r.source}`;
}

const values = <T,>(rows: T[], f: (r: T) => number | null) => rows.map(f);
const times = (rows: { t: number }[]) => rows.map((r) => r.t);

function Panels({
  project,
  w,
  selected,
  picked,
  onZoom,
  onSelect,
}: {
  project: Project;
  w: Window;
  selected: Selected | null;
  picked: Curve | null;
  onZoom: (from: number, to: number) => void;
  onSelect: (s: Selected | null) => void;
}) {
  const span = { from: w.t0, to: w.t1, pauses: w.pauses, onZoom };
  const m = w.machine;
  const b = w.batch;
  const cpu: Series[] = [
    {
      label: "Mean",
      values: values(m, (r) => r.cpu_cores_avg),
      color: "series-1",
    },
    {
      label: "Peak",
      values: values(m, (r) => r.cpu_cores_max),
      color: "series-1",
      dashed: true,
    },
    {
      label: "Load (1 min)",
      values: values(m, (r) => r.load1_max),
      color: "series-2",
    },
  ];
  const mem: Series[] = [
    {
      label: "Mean",
      values: values(m, (r) => r.mem_used_bytes_avg),
      color: "series-1",
    },
    {
      label: "Peak",
      values: values(m, (r) => r.mem_used_bytes_max),
      color: "series-1",
      dashed: true,
    },
    {
      label: "Swap",
      values: values(m, (r) => r.swap_used_bytes_max),
      color: "series-2",
    },
  ];
  const counts: Series[] = [
    {
      label: "Attempts",
      values: values(b, (r) => r.attempts_max),
      color: "series-1",
    },
    {
      label: "Sessions",
      values: values(b, (r) => r.sessions_max),
      color: "series-2",
    },
    {
      label: "Waiting for memory",
      values: values(b, (r) => r.pool_waiting_max),
      color: "series-3",
    },
  ];
  const pool: Series[] = [
    {
      label: "Reserved, mean",
      values: values(b, (r) => r.pool_reserved_bytes_avg),
      color: "series-1",
    },
    {
      label: "Reserved, peak",
      values: values(b, (r) => r.pool_reserved_bytes_max),
      color: "series-1",
      dashed: true,
    },
    {
      label: "Pool",
      values: values(b, (r) => r.pool_bytes),
      color: "series-3",
    },
  ];
  const window = `${epoch(w.t0)} – ${epoch(w.t1)} (${duration(w.t1 - w.t0)})`;
  const legend = (
    <span className="inline-flex flex-wrap items-center gap-x-3">
      <span>{window}</span>
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block size-3 rounded-row border border-warn bg-warn-bg"
          aria-hidden
        />
        Paused after a 429 ({w.pauses.length})
      </span>
      <span>Drag across a chart to zoom</span>
    </span>
  );
  const pickedBar =
    selected?.kind === "container"
      ? w.bars.find((bar) => bar.container_id === selected.id)
      : undefined;
  return (
    <>
      <Card title="Machine" aside={legend}>
        <div className="grid gap-5">
          <Chart
            title="CPU"
            times={times(m)}
            series={cpu}
            max={w.cpus}
            format={cores}
            {...span}
          />
          <Chart
            title="Memory used"
            times={times(m)}
            series={mem}
            max={w.mem_total_bytes}
            format={bytes}
            {...span}
          />
        </div>
      </Card>
      <Card title="Batch">
        {b.length === 0 ? (
          <p className="text-fg-3">This run sampled no batch.</p>
        ) : (
          <div className="grid gap-5">
            <Chart
              title="Running"
              times={times(b)}
              series={counts}
              format={(v) => v.toFixed(0)}
              {...span}
            />
            <Chart
              title="Memory pool"
              times={times(b)}
              series={pool}
              format={bytes}
              {...span}
            />
          </div>
        )}
      </Card>
      {selected !== null && (
        <Card
          title={
            selected.kind === "attempt"
              ? `Attempt ${selected.id}`
              : `Container ${pickedBar ? barLabel(project, pickedBar) : selected.id.slice(0, 12)}`
          }
          aside={
            <button
              type="button"
              className={control}
              onClick={() => {
                onSelect(null);
              }}
            >
              <X className="size-3.5 text-icon" aria-hidden />
              Close
            </button>
          }
        >
          {picked === null ? (
            <p className="text-fg-3">Loading…</p>
          ) : picked.length === 0 ? (
            <p className="text-fg-3">No sample of it in this window.</p>
          ) : (
            <div className="grid gap-5">
              <Chart
                title="CPU"
                times={times(picked)}
                series={[
                  {
                    label: "Mean",
                    values: values(picked, (r) => r.cpu_cores_avg),
                    color: "series-1",
                  },
                  {
                    label: "Peak",
                    values: values(picked, (r) => r.cpu_cores_max),
                    color: "series-1",
                    dashed: true,
                  },
                ]}
                format={cores}
                {...span}
              />
              <Chart
                title="Memory"
                times={times(picked)}
                series={[
                  {
                    label: "Mean",
                    values: values(picked, (r) => r.mem_bytes_avg),
                    color: "series-1",
                  },
                  {
                    label: "Peak",
                    values: values(picked, (r) => r.mem_bytes_max),
                    color: "series-1",
                    dashed: true,
                  },
                ]}
                format={bytes}
                {...span}
              />
            </div>
          )}
        </Card>
      )}
      <Card
        title="Containers"
        aside={
          <span className="inline-flex flex-wrap items-center gap-x-3">
            <span className="inline-flex items-center gap-1.5">
              <Swatch color="bar" dashed={false} />
              Ran
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Swatch color="bad" dashed={false} />
              Exited non-zero
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Swatch color="fg" dashed={false} />
              Chosen
            </span>
          </span>
        }
      >
        <Bars
          project={project}
          bars={w.bars}
          total={w.bars_total}
          until={w.bars_until}
          from={w.t0}
          to={w.t1}
          selected={selected}
          onSelect={onSelect}
        />
      </Card>
    </>
  );
}
