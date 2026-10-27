import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import older from "../../../fixtures/oldrec/attempts/older/task-0301/record.json";
import { sampleProject } from "../../../fixtures/sample";
import type { Project, ProjectTab, TabVersions } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { AttemptPage } from "../attempt/AttemptPage";

const notesPart = {
  name: "notes",
  title: "Notes",
  file: "out/notes.md",
  fields: [
    { title: "Verdict", format: "text" as const },
    { title: "Outcome", format: "text" as const, labels: { done: "Done" } },
    { title: "Spent", format: "usd" as const },
  ],
};

const project: Project = {
  ...sampleProject,
  tabs: [
    {
      name: "notes",
      title: "Notes",
      versions: [
        { pattern: "notes", parts: [notesPart] },
        { pattern: "notes-*", parts: [notesPart] },
      ],
    },
  ],
};

const field = (value: unknown) => ({
  read: "found" as const,
  value,
  reason: null,
});
const notRun = (why: string) => ({
  read: "not run" as const,
  value: null,
  reason: why,
});
const missing = (why: string) => ({
  read: "missing" as const,
  value: null,
  reason: why,
});

const three: TabVersions[] = [
  {
    name: "notes",
    versions: [
      {
        dir: "notes",
        source: 0,
        parts: [
          {
            name: "notes",
            fields: [
              field(null),
              notRun("notes/status.json: No such file or directory"),
              field(1.5),
            ],
          },
        ],
      },
      {
        dir: "notes-2",
        source: 1,
        parts: [
          { name: "notes", fields: [field("n/a"), field("done"), field(0.25)] },
        ],
      },
      {
        dir: "notes-1",
        source: 1,
        parts: [
          {
            name: "notes",
            fields: [
              missing("notes-1/tags.json has no verdict.value"),
              field("y"),
              field(0),
            ],
          },
        ],
      },
    ],
  },
];

/** Answer each route of the API as the backend would for one attempt. */
function serve(versions: TabVersions[], files: Record<string, string>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input, "http://viz");
      if (url.pathname.endsWith("/record"))
        return Promise.resolve(Response.json(older));
      if (url.pathname.endsWith("/live"))
        return Promise.resolve(
          Response.json({ live: null, held: null, current: null }),
        );
      if (url.pathname.endsWith("/tabs"))
        return Promise.resolve(Response.json(versions));
      const path = url.searchParams.get("path") ?? "";
      const text = files[path];
      return Promise.resolve(
        text === undefined
          ? Response.json(
              {
                error: "not found",
                reason: `${path}: No such file or directory`,
              },
              { status: 404 },
            )
          : new Response(text),
      );
    }),
  );
}

function show(at = "&tab=notes", shown = project) {
  render(
    <ProjectsContext value={{ state: "ok", data: [shown] }}>
      <MemoryRouter
        initialEntries={[`/${shown.id}/attempt?id=older/task-0301${at}`]}
      >
        <Routes>
          <Route path=":project/attempt" element={<AttemptPage />} />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const nav = () => screen.getByRole("navigation", { name: "Attempt" });

/** The column of a tab's versions. */
const versionColumn = (tab = "Notes") =>
  within(screen.getByRole("navigation", { name: `${tab} versions` }));

/** Open a version from its entry in the column. */
async function pick(name: string, tab = "Notes") {
  await screen.findByRole("navigation", { name: `${tab} versions` });
  fireEvent.click(versionColumn(tab).getByRole("link", { name }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a project's tab of a Markdown file", () => {
  it("comes after the deliverables' tabs, with its versions as its count, and opens by its link", async () => {
    serve(three, { "notes/out/notes.md": "# First notes\n" });
    show("");

    await screen.findByRole("navigation", { name: "Attempt" });
    const tab = await within(nav()).findByRole("link", { name: /^Notes/ });
    expect(await within(tab).findByText("3")).toBeVisible();
    expect(within(nav()).getAllByRole("link").at(-1)).toBe(tab);
    expect(tab.getAttribute("href")).toContain("tab=notes");
  });

  it("shows the first version by default, named, with its fields as they are", async () => {
    serve(three, { "notes/out/notes.md": "# First notes\n" });
    show();

    expect(
      await screen.findByRole("heading", { name: "First notes" }),
    ).toBeVisible();
    expect(
      versionColumn().getByRole("link", { name: "notes" }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("notes/out/notes.md")).toBeVisible();
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    const value = (title: string) =>
      fields.getByText(title).nextElementSibling?.textContent;
    expect(value("Verdict")).toBe("Not given");
    expect(value("Outcome")).toBe("Not run: no status.json was written");
    expect(value("Spent")).toBe("$1.50");
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
  });

  it("shows a field's value by its label, and one with no label as it is", async () => {
    serve(three, { "notes-2/out/notes.md": "# Second notes\n" });
    show();
    await pick("notes-2");

    expect(
      await screen.findByRole("heading", { name: "Second notes" }),
    ).toBeVisible();
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    const value = (title: string) =>
      fields.getByText(title).nextElementSibling?.textContent;
    expect(value("Outcome")).toBe("Done");
    expect(value("Verdict")).toBe("n/a");
  });

  it("shows a field's list value as a list, an item a line", async () => {
    const listed = three.map((t) => ({
      ...t,
      versions: t.versions.map((v) =>
        v.dir === "notes"
          ? {
              ...v,
              parts: v.parts.map((p) => ({
                ...p,
                fields: [field(["X-1", "done"]), ...p.fields.slice(1)],
              })),
            }
          : v,
      ),
    }));
    serve(listed, { "notes/out/notes.md": "# First notes\n" });
    show();

    await screen.findByRole("heading", { name: "First notes" });
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    const cell = fields.getByText("Verdict").nextElementSibling;
    if (!(cell instanceof HTMLElement)) throw new Error("no value");
    expect(
      within(cell)
        .getAllByRole("listitem")
        .map((i) => i.textContent),
    ).toEqual(["X-1", "done"]);
  });

  it("tells a field whose file is there without its keys from one not run", async () => {
    serve(three, { "notes-1/out/notes.md": "# Old notes\n" });
    show();
    await pick("notes-1");

    expect(
      await screen.findByRole("heading", { name: "Old notes" }),
    ).toBeVisible();
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    expect(fields.getByText("Verdict").nextElementSibling).toHaveTextContent(
      /^notes-1\/tags\.json has no verdict\.value$/,
    );
    expect(fields.getByText("$0.00")).toBeVisible();
  });

  it("shows another version chosen from its column, a group for each pattern", async () => {
    serve(three, {
      "notes/out/notes.md": "# First notes\n",
      "notes-2/out/notes.md": "# Second notes\n",
    });
    show();
    await screen.findByRole("navigation", { name: "Notes versions" });
    expect(
      versionColumn()
        .getAllByRole("group")
        .map((g) =>
          within(g)
            .getAllByRole("link")
            .map((l) => l.textContent),
        ),
    ).toEqual([["notes"], ["notes-2", "notes-1"]]);
    expect(
      versionColumn().getByRole("link", { name: "notes-2" }),
    ).toHaveAttribute(
      "href",
      `/${project.id}/attempt?id=older%2Ftask-0301&tab=notes&version=notes-2`,
    );
    await pick("notes-2");

    expect(
      await screen.findByRole("heading", { name: "Second notes" }),
    ).toBeVisible();
    expect(
      versionColumn().getByRole("link", { name: "notes-2" }),
    ).toHaveAttribute("aria-current", "page");
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    expect(fields.getByText("n/a")).toBeVisible();
    expect(fields.getByText("$0.25")).toBeVisible();
  });

  it("is there with Not run when the attempt has no version yet", async () => {
    serve([{ name: "notes", versions: [] }], {});
    show();

    expect(
      await screen.findByText("Not run: this attempt has no Notes yet."),
    ).toBeVisible();
    expect(
      within(nav()).getByRole("link", { name: /^Notes/ }),
    ).toHaveTextContent("NotesNot run");
  });

  it("names each version by its version_title, by its directory where it has none", async () => {
    const time = { format: "time" as const };
    const titled: Project = {
      ...project,
      tabs: project.tabs?.map((t) => ({
        ...t,
        versions: [
          { pattern: "notes", parts: [notesPart], version_title: time },
          { pattern: "notes-*", parts: [notesPart], version_title: time },
        ],
      })),
    };
    const [notes] = three;
    const versions = notes?.versions ?? [];
    serve(
      [
        {
          name: "notes",
          versions: versions.map((v, i) => ({
            ...v,
            title: [
              field("2026-03-02T09:15:00Z"),
              field("2026-03-01T08:00:00Z"),
              notRun(`${v.dir}/status.json: No such file or directory`),
            ][i],
          })),
        },
      ],
      { "notes/out/notes.md": "# First notes\n" },
    );
    show("&tab=notes", titled);
    await screen.findByRole("navigation", { name: "Notes versions" });

    expect(
      versionColumn()
        .getAllByRole("link")
        .map((l) => l.textContent),
    ).toEqual(["2026-03-02 09:15 UTC", "2026-03-01 08:00 UTC", "notes-1"]);
  });

  it("says Not run for a version whose Markdown is not there yet", async () => {
    serve(three, {});
    show();

    expect(
      await screen.findByText("Not run: no out/notes.md was written"),
    ).toBeVisible();
    expect(screen.getByText("$1.50")).toBeVisible();
  });

  it("keeps raw HTML in the Markdown as text", async () => {
    serve(three, {
      "notes/out/notes.md": '<script>alert("x")</script>\n\n<b>bold</b>\n',
    });
    show();

    expect(
      await screen.findByText('<script>alert("x")</script>', { exact: false }),
    ).toBeVisible();
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
  });
});

const postParts: ProjectTab["versions"][number]["parts"] = [
  {
    name: "report",
    title: "Report",
    file: "out/report.md",
    fields: [{ title: "Verdict", format: "text" }],
  },
  {
    name: "prompt",
    title: "Prompt",
    file: "prompt.md",
    fields: [{ title: "Words", format: "number" }],
  },
];

const parted: Project = {
  ...sampleProject,
  tabs: [
    {
      name: "post",
      title: "Post",
      versions: [
        { pattern: "post", parts: postParts },
        { pattern: "post-*", parts: postParts },
      ],
    },
  ],
};

const posts: TabVersions[] = [
  {
    name: "post",
    versions: ["post", "post-1"].map((dir, source) => ({
      dir,
      source,
      parts: [
        { name: "report", fields: [field(`kept in ${dir}`)] },
        { name: "prompt", fields: [field(dir.length * 100)] },
      ],
    })),
  },
];

const both = {
  "post/out/report.md": "# Latest report\n",
  "post/prompt.md": "# Latest prompt\n",
  "post-1/out/report.md": "# Earlier report\n",
  "post-1/prompt.md": "# Earlier prompt\n",
};

const column = () =>
  within(screen.getByRole("navigation", { name: "Post versions" }));

describe("a project's tab of several parts", () => {
  it("lists its parts in a column and shows the first, with its fields", async () => {
    serve(posts, both);
    show("&tab=post", parted);

    expect(
      await screen.findByRole("heading", { name: "Latest report" }),
    ).toBeVisible();
    const links = column().getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "post",
      "Report",
      "Prompt",
      "post-1",
    ]);
    expect(links[0]).toHaveAttribute("aria-current", "true");
    expect(links[1]).toHaveAttribute("aria-current", "page");
    expect(links[2]).toHaveAttribute(
      "href",
      `/${parted.id}/attempt?id=older%2Ftask-0301&tab=post&version=post&part=prompt`,
    );
    const fields = within(
      screen.getByRole("region", { name: "Report fields" }),
    );
    expect(fields.getByText("kept in post")).toBeVisible();
    expect(screen.getByText("post/out/report.md")).toBeVisible();
  });

  it("opens the part its link names", async () => {
    serve(posts, both);
    show("&tab=post", parted);
    await screen.findByRole("heading", { name: "Latest report" });

    fireEvent.click(column().getByRole("link", { name: "Prompt" }));

    expect(
      await screen.findByRole("heading", { name: "Latest prompt" }),
    ).toBeVisible();
    expect(column().getByRole("link", { name: "Prompt" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText("post/prompt.md")).toBeVisible();
    const fields = within(
      screen.getByRole("region", { name: "Prompt fields" }),
    );
    expect(fields.getByText("Words").nextElementSibling?.textContent).toBe(
      "400",
    );
    expect(screen.queryByRole("region", { name: "Report fields" })).toBeNull();
  });

  it("opens the part the URL names, and the first for a name it has not", async () => {
    serve(posts, both);
    show("&tab=post&part=prompt", parted);

    expect(
      await screen.findByRole("heading", { name: "Latest prompt" }),
    ).toBeVisible();
    cleanup();
    show("&tab=post&part=summary", parted);

    expect(
      await screen.findByRole("heading", { name: "Latest report" }),
    ).toBeVisible();
    expect(column().getByRole("link", { name: "Report" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("keeps the version chosen as another part is shown", async () => {
    serve(posts, both);
    show("&tab=post", parted);
    await pick("post-1", "Post");
    expect(
      await screen.findByRole("heading", { name: "Earlier report" }),
    ).toBeVisible();
    expect(screen.getByText("kept in post-1")).toBeVisible();
    fireEvent.click(column().getByRole("link", { name: "Prompt" }));

    expect(
      await screen.findByRole("heading", { name: "Earlier prompt" }),
    ).toBeVisible();
    expect(column().getByRole("link", { name: "post-1" })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("says Not run for a part whose file is not there", async () => {
    serve(posts, { "post/out/report.md": "# Latest report\n" });
    show("&tab=post&part=prompt", parted);

    expect(
      await screen.findByText("Not run: no prompt.md was written"),
    ).toBeVisible();
  });

  it("says Not run, with no column, when the attempt has no version yet", async () => {
    serve([{ name: "post", versions: [] }], {});
    show("&tab=post", parted);

    expect(
      await screen.findByText("Not run: this attempt has no Post yet."),
    ).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Post" })).toBeNull();
    expect(
      screen.queryByRole("navigation", { name: "Post versions" }),
    ).toBeNull();
  });
});

describe("a project's tab with no version, that names its job", () => {
  const withJob: Project = {
    ...project,
    jobs: [
      { action: "note", label: "Notes", queue: "slow", subject: "attempt" },
    ],
    tabs: project.tabs?.map((t) => ({ ...t, job: "note" })),
  };
  const job = {
    id: 98,
    queue: "slow",
    labels: { action: "note", item: "older/task-0301" },
    state: "canceled",
    memory_mib: 1,
    queued_at: 1772442900,
    started_at: null,
    ended_at: 1772443200,
    exit_code: null,
    cancel_requested_at: 1772443200,
  };
  const pause = {
    queue: "slow",
    paused_at: 1772442000,
    reason: "held until asked",
  };

  it("says how its newest job stands, and that its queue is paused", async () => {
    serve(
      [
        {
          name: "notes",
          queued: { job, paused: pause, reason: null },
          versions: [],
        },
      ],
      {},
    );
    show("&tab=notes", withJob);

    expect(
      await screen.findByText("Not run: this attempt has no Notes yet."),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Job 98" })).toHaveAttribute(
      "href",
      "/queue",
    );
    expect(screen.getByText("Canceled")).toBeVisible();
    expect(
      screen.getByText(
        "queued 2026-03-02 09:15 UTC · ended 2026-03-02 09:20 UTC",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "The slow queue has been paused since 2026-03-02 09:00 UTC: held until asked.",
      ),
    ).toBeVisible();
  });

  it("says why how its job stands is not known when the queue cannot be read", async () => {
    serve(
      [
        {
          name: "notes",
          queued: {
            job: null,
            paused: null,
            reason: "the queue cannot be read: file is not a database",
          },
          versions: [],
        },
      ],
      {},
    );
    show("&tab=notes", withJob);

    expect(
      await screen.findByText(
        "How its job stands is not known: the queue cannot be read: file is not a database.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("No job for it has been queued.")).toBeNull();
  });

  it("says when no job for it has been queued, and nothing of a queue not paused", async () => {
    serve(
      [
        {
          name: "notes",
          queued: { job: null, paused: null, reason: null },
          versions: [],
        },
      ],
      {},
    );
    show("&tab=notes", withJob);

    expect(
      await screen.findByText("No job for it has been queued."),
    ).toBeVisible();
    expect(screen.queryByText(/paused/)).toBeNull();
  });
});

const reviewParts: ProjectTab["versions"][number]["parts"] = [
  { name: "summary", title: "Summary", file: "summary.md", fields: [] },
  { name: "review", title: "Review", file: "out/review.md", fields: [] },
];

const sourced: Project = {
  ...sampleProject,
  tabs: [
    {
      name: "reviews",
      title: "Reviews",
      versions: [
        {
          pattern: "review-*",
          parts: reviewParts,
          version_title: { format: "text" },
        },
        {
          pattern: "runs/*",
          parts: [
            { name: "reviews", title: "Reviews", file: "index.md", fields: [] },
          ],
          version_title: { format: "text" },
        },
        { pattern: "runs/*/trial-*", parts: reviewParts },
      ],
    },
  ],
};

const reviews: TabVersions[] = [
  {
    name: "reviews",
    versions: [
      { dir: "review-2", source: 0, title: field("second") },
      { dir: "review-1", source: 0, title: notRun("no status.json") },
      { dir: "runs/3", source: 1, label: "Run 3", title: field("merged") },
      { dir: "runs/2", source: 1, label: "Run 2", title: notRun("none") },
      { dir: "runs/3/trial-1", source: 2, label: "Run 3, trial 1" },
    ].map((v) => ({
      ...v,
      parts:
        sourced.tabs?.[0]?.versions[v.source]?.parts.map((p) => ({
          name: p.name,
          fields: [],
        })) ?? [],
    })),
  },
];

describe("a project's tab whose patterns go several levels down", () => {
  it("groups the versions of a pattern others lie under, each with those that lie in it", async () => {
    serve(reviews, { "review-2/summary.md": "# Second\n" });
    show("&tab=reviews", sourced);
    await screen.findByRole("navigation", { name: "Reviews versions" });

    // A pattern no other lies under is one group with no label; each
    // version of runs/* heads its own, by its label, its entry its value,
    // else its directory, its trial's its directory for a label with no
    // " › "; the parts of the version shown under its entry.
    const groups = versionColumn("Reviews").getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual([
      null,
      "Run 3",
      "Run 2",
    ]);
    expect(
      groups.map((g) =>
        within(g)
          .getAllByRole("link")
          .map((l) => l.textContent),
      ),
    ).toEqual([
      ["second", "Summary", "Review", "review-1"],
      ["merged", "runs/3/trial-1"],
      ["runs/2"],
    ]);
  });

  it("shows a version's files from its path, with its own pattern's parts, and opens those in it from its top", async () => {
    serve(reviews, {
      "review-2/summary.md": "# Second\n",
      "runs/3/index.md": "# Merged\n",
      "runs/3/trial-1/summary.md": "# Trial summary\n",
    });
    show("&tab=reviews&part=summary", sourced);
    await screen.findByRole("heading", { name: "Second" });
    expect(
      screen.queryByRole("navigation", { name: /^Versions under/ }),
    ).toBeNull();

    await pick("merged", "Reviews");

    expect(
      await screen.findByRole("heading", { name: "Merged" }),
    ).toBeVisible();
    expect(screen.getByText("runs/3/index.md")).toBeVisible();
    expect(screen.getByText("Run 3 · merged")).toBeVisible();
    const under = within(
      screen.getByRole("navigation", { name: "Versions under Run 3 · merged" }),
    );

    fireEvent.click(under.getByRole("link", { name: "runs/3/trial-1" }));

    expect(
      await screen.findByRole("heading", { name: "Trial summary" }),
    ).toBeVisible();
    expect(screen.getByText("runs/3/trial-1/summary.md")).toBeVisible();
    expect(
      within(versionColumn("Reviews").getAllByRole("group")[1] ?? document.body)
        .getAllByRole("link")
        .map((l) => l.textContent),
    ).toEqual(["merged", "runs/3/trial-1", "Summary", "Review"]);
  });
});

const visited: Project = {
  ...sampleProject,
  tabs: [
    {
      name: "audit",
      title: "Audit",
      versions: [
        {
          pattern: "runs/*",
          parts: [
            { name: "audit", title: "Audit", file: "index.md", fields: [] },
          ],
        },
        { pattern: "runs/*/trial-*", parts: reviewParts },
        {
          pattern: "offline-*",
          parts: reviewParts,
          version_title: { format: "text" },
        },
      ],
    },
  ],
};

const visits: TabVersions[] = [
  {
    name: "audit",
    versions: [
      { dir: "runs/3", source: 0, label: "Run 3 › Merged" },
      { dir: "runs/2", source: 0, label: "Run 2 › Merged" },
      { dir: "runs/3/trial-2", source: 1, label: "Run 3 › Trial 2" },
      { dir: "runs/3/trial-1", source: 1, label: "Run 3 › Trial 1" },
      {
        dir: "offline-2",
        source: 2,
        label: "Offline",
        title: field("second"),
      },
      {
        dir: "offline-1",
        source: 2,
        label: "Offline",
        title: notRun("no status.json"),
      },
    ].map((v) => ({
      ...v,
      parts:
        visited.tabs?.[0]?.versions[v.source]?.parts.map((p) => ({
          name: p.name,
          fields: [],
        })) ?? [],
    })),
  },
];

describe("a project's tab whose labels name a group before ' › '", () => {
  it("heads each group by the text before it, and its entries by the text after", async () => {
    serve(visits, { "runs/3/index.md": "# Merged\n" });
    show("&tab=audit", visited);
    await screen.findByRole("heading", { name: "Merged" });

    const groups = versionColumn("Audit").getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual([
      "Run 3",
      "Run 2",
      "Offline",
    ]);
    // Those in a version in the order of their names, unlike the tab's.
    expect(
      groups.map((g) =>
        within(g)
          .getAllByRole("link")
          .map((l) => l.textContent),
      ),
    ).toEqual([
      ["Merged", "Trial 1", "Trial 2"],
      ["Merged"],
      ["second", "offline-1"],
    ]);
    expect(screen.getByText("Run 3 › Merged")).toBeVisible();
  });

  it("opens each version that lies in the one shown from a row at its top", async () => {
    serve(visits, {
      "runs/3/index.md": "# Merged\n",
      "runs/3/trial-2/summary.md": "# Second trial\n",
    });
    show("&tab=audit", visited);
    await screen.findByRole("heading", { name: "Merged" });
    const under = within(
      screen.getByRole("navigation", { name: "Versions under Run 3 › Merged" }),
    );
    expect(under.getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Trial 1",
      "Trial 2",
    ]);

    fireEvent.click(under.getByRole("link", { name: "Trial 2" }));

    expect(
      await screen.findByRole("heading", { name: "Second trial" }),
    ).toBeVisible();
    expect(screen.getByText("Run 3 › Trial 2")).toBeVisible();
    expect(
      screen.queryByRole("navigation", { name: /^Versions under/ }),
    ).toBeNull();
  });

  it("opens the version the URL names, and the first for a directory it has not", async () => {
    serve(visits, {
      "runs/3/index.md": "# Merged\n",
      "offline-2/summary.md": "# Offline summary\n",
    });
    show("&tab=audit&version=offline-2", visited);

    expect(
      await screen.findByRole("heading", { name: "Offline summary" }),
    ).toBeVisible();
    expect(screen.getByText("Offline · second")).toBeVisible();
    cleanup();
    show("&tab=audit&version=runs/9", visited);

    expect(
      await screen.findByRole("heading", { name: "Merged" }),
    ).toBeVisible();
  });
});
