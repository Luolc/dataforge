// The route /<p>: a project's items, one column of IDs with their revisions,
// a search and a refresh. The export holds only the items it shows, so
// there is no status and no filter.
import { RefreshCw, Search } from "lucide-react";
import { use, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { useLoaded } from "../api/client";
import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import { time } from "../ui/format";
import {
  itemHref,
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

function Items({ project }: { project: ShareProject }) {
  const { refused } = use(SignedContext);
  // A refresh reads again, and the last read stays shown until it lands.
  const [generation, setGeneration] = useState(0);
  const loaded = useLoaded(
    project.id,
    async (signal) => ({
      items: await read(
        shareUrls.items(project.id),
        listedItems,
        refused,
        signal,
      ),
      at: new Date().toISOString(),
    }),
    generation,
    { limit: true },
  );
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  if (loaded.state !== "ok")
    return (
      <Note>{loaded.state === "error" ? loaded.message : "Loading…"}</Note>
    );
  const { items, at } = loaded.data;
  const error = loaded.failed?.message ?? null;
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
          <span aria-live="polite">{`Read ${time(at)}`}</span>
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
