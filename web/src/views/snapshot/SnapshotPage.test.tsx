import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { Shell } from "../../shell/Shell";
import { SnapshotPage } from "./SnapshotPage";
import type { Snapshot } from "./types";

const plain = {
  id: "example",
  name: "Example",
  color: "#e5a54b",
  states: {},
  statuses: {},
  item: { depth: 1, title: "Batch" },
  columns: [],
  session: { thinking: "hidden" },
} satisfies Project;

const project = {
  ...plain,
  pages: [
    { kind: "snapshot", name: "steps", title: "Steps", roots: ["bundles"] },
    { kind: "attempts", name: "attempts", title: "Attempts", roots: [] },
  ],
} satisfies Project;

const rows = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    id: `batch-${(i + 1).toString().padStart(3, "0")}`,
    values: {
      state: i % 3 === 0 ? "partial" : "complete",
      files: i + 1,
      cost: 0.5,
      took: 61,
      built: "2026-03-03T11:00:00Z",
    },
    ...(i === 0
      ? { dir: { root: "bundles", path: "batch-001", open: "summary.md" } }
      : {}),
  }));

const snapshot = (n = 3): Snapshot => ({
  version: 1,
  generated_at: "2026-03-03T11:15:00Z",
  statuses: {
    complete: { label: "Complete", tone: "good" },
    partial: { label: "Partial", tone: "warn" },
  },
  steps: [
    {
      name: "gather",
      title: "Gather",
      value: 1200,
      unit: "sources",
      note: "3 kinds of source",
    },
    {
      name: "bundle",
      title: "Bundle",
      value: 2,
      of: 3,
      unit: "batches",
      note: "4 bundles in all",
      generated_at: "2026-03-03T12:00:00Z",
      entries: {
        id_title: "Batch",
        columns: [
          { name: "state", title: "State", format: "status", filter: true },
          { name: "files", title: "Files", format: "number" },
          { name: "cost", title: "Cost", format: "usd" },
          { name: "took", title: "Took", format: "duration" },
          { name: "built", title: "Built", format: "time" },
          { name: "note", title: "Note" },
        ],
        rows: rows(n),
      },
    },
    { name: "attempts", title: "Attempts", value: 1, page: "attempts" },
  ],
});

let answers: Record<string, () => Response>;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  answers = {
    "/api/projects/example/pages/steps/snapshot": () =>
      Response.json(snapshot()),
  };
  fetchMock = vi.fn((url: string) => {
    const answer = answers[url];
    return Promise.resolve(
      answer
        ? answer()
        : Response.json({ error: "not found" }, { status: 404 }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function Where() {
  const { pathname, search } = useLocation();
  return <output aria-label="where">{pathname + search}</output>;
}

function show(url = "/example/steps") {
  return render(
    <ProjectsContext value={{ state: "ok", data: [project] }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path=":project/:page"
            element={
              <>
                <SnapshotPage />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const crumbs = () =>
  within(screen.getByRole("navigation", { name: "Breadcrumb" }));

describe("the snapshot page", () => {
  it("shows a card per step, its number, its share of what it counts out of and its note", async () => {
    show();

    const steps = await screen.findByRole("list", { name: "Steps" });
    const [gather, bundle, attempts] = within(steps).getAllByRole("listitem");
    expect(gather).toHaveTextContent("Gather1,200sources3 kinds of source");
    expect(bundle).toHaveTextContent("Bundle2/ 3batches4 bundles in all");
    expect(bundle?.querySelector(".bg-accent")).toHaveStyle({
      width: `${((2 / 3) * 100).toString()}%`,
    });
    expect(gather?.querySelector(".bg-accent")).toBeNull();
    expect(screen.getByText(/^Snapshot of 2026-03-03/)).toBeInTheDocument();
    expect(within(gather ?? document.body).queryByRole("link")).toBeNull();
    expect(within(bundle ?? document.body).getByRole("link")).toHaveAttribute(
      "href",
      "/example/steps?step=bundle",
    );
    expect(within(attempts ?? document.body).getByRole("link")).toHaveAttribute(
      "href",
      "/example",
    );
    expect(crumbs().getByText("Steps")).toHaveAttribute("aria-current", "page");
    expect(crumbs().getByRole("link", { name: "Example" })).toHaveAttribute(
      "href",
      "/example/steps",
    );
  });

  it("says no snapshot has been written yet, naming the file, as a state and not an error", async () => {
    answers["/api/projects/example/pages/steps/snapshot"] = () =>
      Response.json(
        {
          error: "no snapshot",
          reason: "no snapshot has been written",
          file: "../snapshot/steps.json",
        },
        { status: 404 },
      );
    show();

    expect(
      await screen.findByText("No snapshot has been written yet."),
    ).toBeInTheDocument();
    expect(screen.getByText("../snapshot/steps.json")).toBeInTheDocument();
    expect(screen.queryByText(/cannot be read/)).toBeNull();
  });

  it("says why a snapshot the check refused cannot be read", async () => {
    answers["/api/projects/example/pages/steps/snapshot"] = () =>
      Response.json(
        { error: "unavailable", reason: "the snapshot: steps: empty" },
        { status: 503 },
      );
    show();

    expect(
      await screen.findByText(
        "The snapshot cannot be read: 503: the snapshot: steps: empty",
      ),
    ).toBeInTheDocument();
  });

  it("has no page for a name the project gives no snapshot page", () => {
    show("/example/attempts");

    expect(screen.getByText("No page here.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("a step's table", () => {
  it("shows each value by its column's format, numbers right-aligned in tabular figures", async () => {
    show("/example/steps?step=bundle");

    const table = await screen.findByRole("table");
    const [head, first] = within(table).getAllByRole("row");
    expect(
      within(head ?? table)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["Batch", "State", "Files", "Cost", "Took", "Built", "Note"]);
    const cells = within(first ?? table).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual([
      "Partial",
      "1",
      "$0.50",
      "1m 1s",
      expect.stringMatching(/^2026-03-03 /),
      "—",
    ]);
    for (const i of [1, 2, 3])
      expect(cells[i]).toHaveClass("text-right", "tabular-nums");
    expect(cells[0]).not.toHaveClass("text-right");
    expect(
      within(head ?? table).getByRole("columnheader", { name: "Files" }),
    ).toHaveClass("text-right");
    expect(crumbs().getByRole("link", { name: "Steps" })).toHaveAttribute(
      "href",
      "/example/steps",
    );
    expect(crumbs().getByText("Bundle")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("links a row with a directory to it, and no other", async () => {
    show("/example/steps?step=bundle");

    const table = await screen.findByRole("table");
    expect(
      within(table).getByRole("link", { name: "batch-001" }),
    ).toHaveAttribute("href", "/example/steps?step=bundle&entry=batch-001");
    expect(within(table).queryByRole("link", { name: "batch-002" })).toBeNull();
  });

  it("filters by a column's chips and says how many rows they hide", async () => {
    show("/example/steps?step=bundle");

    const group = await screen.findByRole("group", { name: "Filter by State" });
    expect(
      within(group)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["All3", "Partial1", "Complete2"]);
    fireEvent.click(within(group).getByRole("button", { name: /Partial/ }));

    expect(screen.getByLabelText("where")).toHaveTextContent(
      "/example/steps?step=bundle&column-state=partial",
    );
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(
      screen.getByText(/2 of 3 rows hidden by the State filter\./),
    ).toBeInTheDocument();
  });

  it("searches the IDs, the search counted among what hides rows", async () => {
    show("/example/steps?step=bundle&q=002&column-state=complete");

    await screen.findByRole("table");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(
      screen.getByText(
        /2 of 3 rows hidden by the search and the State filter\./,
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getByLabelText("where")).toHaveTextContent(
      /^\/example\/steps\?step=bundle$/,
    );
  });

  it("draws 50 rows a page of a table of thousands, with the page's span", async () => {
    answers["/api/projects/example/pages/steps/snapshot"] = () =>
      Response.json(snapshot(1234));
    show("/example/steps?step=bundle&n=2");

    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(51);
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("batch-051");
    const pages = screen.getByRole("navigation", {
      name: "Pages of the table",
    });
    expect(pages).toHaveTextContent("Rows 51–100 of 1,234 · page 2 of 25");
    expect(
      within(pages).getByRole("link", { name: "Previous" }),
    ).toHaveAttribute("href", "/example/steps?step=bundle");
    expect(within(pages).getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/example/steps?step=bundle&n=3",
    );
  });

  it("holds a page number past the last to the last page", async () => {
    answers["/api/projects/example/pages/steps/snapshot"] = () =>
      Response.json(snapshot(60));
    show("/example/steps?step=bundle&n=9");

    await screen.findByRole("table");
    const pages = screen.getByRole("navigation", {
      name: "Pages of the table",
    });
    expect(pages).toHaveTextContent("Rows 51–60 of 60 · page 2 of 2");
    expect(within(pages).queryByRole("link", { name: "Next" })).toBeNull();
  });
});

describe("a row's directory", () => {
  it("shows the row's values over its files, read from the page's root, the file it names first", async () => {
    answers[
      "/api/projects/example/pages/steps/files?root=bundles&path=batch-001"
    ] = () =>
      Response.json({
        entries: [{ name: "summary.md", type: "file", size: 10 }],
      });
    answers[
      "/api/projects/example/pages/steps/files?root=bundles&path=batch-001%2Fsummary.md"
    ] = () =>
      new Response("# Batch 1\n", {
        headers: { "content-type": "application/octet-stream" },
      });
    show("/example/steps?step=bundle&entry=batch-001");

    expect(
      await screen.findByRole("heading", { name: "Batch 1" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "batch-001" }),
    ).toBeInTheDocument();
    expect(screen.getByText("State").nextSibling).toHaveTextContent("Partial");
    expect(crumbs().getByRole("link", { name: "Bundle" })).toHaveAttribute(
      "href",
      "/example/steps?step=bundle",
    );
    expect(crumbs().getByText("batch-001")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("says a row the step does not have is not there", async () => {
    show("/example/steps?step=bundle&entry=batch-999");

    expect(
      await screen.findByText("Bundle has no row batch-999."),
    ).toBeInTheDocument();
  });
});

describe("the sidebar's pages", () => {
  function shell(url: string, projects: Project[] = [project]) {
    answers["/api/projects"] = () => Response.json(projects);
    return render(
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="*" element={null} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  }

  const site = () =>
    within(
      screen.getAllByRole("navigation", { name: "Site" })[0] ?? document.body,
    );

  it("puts a project's pages under its name, which opens the first", async () => {
    shell("/example/steps");

    const name = await site().findByRole("link", { name: "Example" });
    expect(name).toHaveAttribute("href", "/example/steps");
    expect(name).toHaveClass("font-semibold");
    expect(name).not.toHaveAttribute("aria-current");
    const steps = site().getByRole("link", { name: "Steps" });
    const attempts = site().getByRole("link", { name: "Attempts" });
    expect(steps).toHaveAttribute("aria-current", "page");
    expect(steps).toHaveClass("target", "text-faint");
    expect(attempts).toHaveAttribute("href", "/example");
    expect(attempts).not.toHaveAttribute("aria-current");
  });

  it("marks the attempts page while one of its attempts is shown, not as the page", async () => {
    shell("/example/attempt?id=batch-1/task-1");

    const attempts = await site().findByRole("link", { name: "Attempts" });
    expect(attempts).toHaveAttribute("aria-current", "true");
    expect(site().getByRole("link", { name: "Steps" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("shows a project with no pages as one entry, as before", async () => {
    shell("/example", [plain]);

    const entry = await site().findByRole("link", { name: "Example" });
    expect(entry).toHaveAttribute("href", "/example");
    expect(entry).toHaveAttribute("aria-current", "page");
    expect(site().queryByRole("link", { name: "Steps" })).toBeNull();
  });
});
