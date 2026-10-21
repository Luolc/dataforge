// The route /<p>: a project's items, one column of IDs with their revisions,
// a search and a refresh. The export holds only the items it shows, so
// there is no status and no filter.
import { RefreshCw, Search } from "lucide-react";
import { use, useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import { time } from "../ui/format";
import {
  itemHref,
  type Listed,
  listedItems,
  read,
  type ShareProject,
  shareUrls,
  shownId,
  SignedContext,
  useShareProject,
} from "./api";

const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover";

export function ItemsPage() {
  const { project: id } = useParams();
  const project = useShareProject(id);
  const name = project.state === "ok" ? project.data.name : (id ?? "");
  return (
    <Frame crumbs={[{ label: name }]}>
      {project.state === "ok" ? (
        <Items key={project.data.id} project={project.data} />
      ) : (
        <Note>{project.state === "error" ? project.message : "Loading…"}</Note>
      )}
    </Frame>
  );
}

/**
 * A project's items, read once and again on each refresh; the last read
 * stays shown while the next one loads.
 */
function useItems(project: string) {
  const { refused } = use(SignedContext);
  const [generation, setGeneration] = useState(0);
  const key = JSON.stringify([project, generation]);
  const [last, setLast] = useState<{
    project: string;
    items: Listed[];
    at: string;
  }>();
  const [done, setDone] = useState<{ key: string; error: string | null }>();
  useEffect(() => {
    const controller = new AbortController();
    read(
      shareUrls.items(project),
      listedItems,
      refused,
      controller.signal,
    ).then(
      (items) => {
        setLast({ project, items, at: new Date().toISOString() });
        setDone({ key, error: null });
      },
      (e: unknown) => {
        if (controller.signal.aborted) return;
        setDone({ key, error: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => {
      controller.abort();
    };
  }, [project, key, refused]);
  const refresh = useCallback(() => {
    setGeneration((g) => g + 1);
  }, []);
  return {
    last: last?.project === project ? last : null,
    error: done?.key === key ? done.error : null,
    reading: done?.key !== key,
    refresh,
  };
}

function Items({ project }: { project: ShareProject }) {
  const { last, error, reading, refresh } = useItems(project.id);
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  if (last === null) return <Note>{error ?? "Loading…"}</Note>;
  const { items, at } = last;
  const needle = q.trim().toLowerCase();
  const shown = items.filter((item) =>
    shownId(item).toLowerCase().includes(needle),
  );
  const hidden = items.length - shown.length;
  const search = (value: string) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value !== "") next.set("q", value);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  };
  return (
    <div className="flex flex-col gap-3 px-6 py-5">
      <div className="flex flex-wrap items-center gap-2">
        <label className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 focus-within:border-fg-3">
          <Search className="size-3.5 text-icon" aria-hidden />
          <input
            type="search"
            aria-label="Search IDs"
            placeholder="Search IDs"
            value={q}
            onChange={(e) => {
              search(e.target.value);
            }}
            className="h-6 w-48 bg-transparent text-xs outline-none placeholder:text-fg-3"
          />
        </label>
        <div className="ml-auto flex items-center gap-2 text-xs text-fg-3">
          {error !== null && <span className="text-bad">{error}</span>}
          <span aria-live="polite">
            {reading ? "Reading…" : `Read ${time(at)}`}
          </span>
          <button type="button" onClick={refresh} className={control}>
            <RefreshCw className="size-3.5 text-icon" aria-hidden />
            Refresh
          </button>
        </div>
      </div>
      {hidden > 0 && (
        <p className="flex items-center gap-2 text-xs text-fg-3">
          {hidden} of {items.length} hidden by the search.
          <button
            type="button"
            className={control}
            onClick={() => {
              search("");
            }}
          >
            Show all
          </button>
        </p>
      )}
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line bg-head text-xs text-fg-2">
            <th scope="col" className="px-3 py-1 text-left font-medium">
              ID
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((item) => (
            <tr key={item.id} className="border-b border-line-subtle">
              <td className="px-3 py-1.5">
                <Link
                  to={itemHref(project.id, item.id)}
                  className="inline-flex target items-center font-mono text-xs break-all underline decoration-faint underline-offset-3"
                >
                  {shownId(item)}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {shown.length === 0 && (
        <p className="py-6 text-center text-fg-3">
          {items.length === 0
            ? "The project has no items."
            : "Nothing matches the search."}
        </p>
      )}
    </div>
  );
}
