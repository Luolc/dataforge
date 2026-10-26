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
      screen.getByRole("button", { name: "Version: notes" }),
    ).toBeVisible();
    expect(screen.getByText("1 of 3")).toBeVisible();
    expect(screen.getByText("notes/out/notes.md")).toBeVisible();
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    const value = (title: string) =>
      fields.getByText(title).nextElementSibling?.textContent;
    expect(value("Verdict")).toBe("Not given");
    expect(value("Outcome")).toBe(
      "Not run: notes/status.json: No such file or directory",
    );
    expect(value("Spent")).toBe("$1.50");
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
  });

  it("shows a field's value by its label, and one with no label as it is", async () => {
    serve(three, { "notes-2/out/notes.md": "# Second notes\n" });
    show();
    const button = await screen.findByRole("button", {
      name: "Version: notes",
    });

    fireEvent.pointerDown(button, { button: 0, ctrlKey: false });
    fireEvent.click(screen.getByRole("menuitem", { name: "notes-2" }));

    expect(
      await screen.findByRole("heading", { name: "Second notes" }),
    ).toBeVisible();
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    const value = (title: string) =>
      fields.getByText(title).nextElementSibling?.textContent;
    expect(value("Outcome")).toBe("Done");
    expect(value("Verdict")).toBe("n/a");
  });

  it("shows a field's list value as its items joined with commas", async () => {
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
    expect(fields.getByText("Verdict").nextElementSibling?.textContent).toBe(
      "X-1, done",
    );
  });

  it("tells a field whose file is there without its keys from one not run", async () => {
    serve(three, { "notes-1/out/notes.md": "# Old notes\n" });
    show();
    const button = await screen.findByRole("button", {
      name: "Version: notes",
    });

    fireEvent.pointerDown(button, { button: 0, ctrlKey: false });
    fireEvent.click(screen.getByRole("menuitem", { name: "notes-1" }));

    expect(
      await screen.findByRole("heading", { name: "Old notes" }),
    ).toBeVisible();
    const fields = within(screen.getByRole("region", { name: "Notes fields" }));
    expect(fields.getByText("Verdict").nextElementSibling).toHaveTextContent(
      /^notes-1\/tags\.json has no verdict\.value$/,
    );
    expect(fields.getByText("$0.00")).toBeVisible();
  });

  it("shows another version chosen from its menu", async () => {
    serve(three, {
      "notes/out/notes.md": "# First notes\n",
      "notes-2/out/notes.md": "# Second notes\n",
    });
    show();
    const button = await screen.findByRole("button", {
      name: "Version: notes",
    });

    fireEvent.pointerDown(button, { button: 0, ctrlKey: false });
    const items = screen.getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "notes",
      "notes-2",
      "notes-1",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "notes-2" }));

    expect(
      await screen.findByRole("heading", { name: "Second notes" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Version: notes-2" }),
    ).toBeVisible();
    expect(screen.getByText("2 of 3")).toBeVisible();
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
    const button = await screen.findByRole("button", {
      name: "Version: 2026-03-02 09:15 UTC",
    });

    fireEvent.pointerDown(button, { button: 0, ctrlKey: false });

    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
      "2026-03-02 09:15 UTC",
      "2026-03-01 08:00 UTC",
      "notes-1",
    ]);
  });

  it("says Not run for a version whose Markdown is not there yet", async () => {
    serve(three, {});
    show();

    expect(
      await screen.findByText(
        "Not run: notes/out/notes.md: No such file or directory",
      ),
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

const column = () => within(screen.getByRole("navigation", { name: "Post" }));

describe("a project's tab of several parts", () => {
  it("lists its parts in a column and shows the first, with its fields", async () => {
    serve(posts, both);
    show("&tab=post", parted);

    expect(
      await screen.findByRole("heading", { name: "Latest report" }),
    ).toBeVisible();
    const links = column().getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["Report", "Prompt"]);
    expect(links[0]).toHaveAttribute("aria-current", "page");
    expect(links[1]).toHaveAttribute(
      "href",
      `/${parted.id}/attempt?id=older%2Ftask-0301&tab=post&part=prompt`,
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
    const button = await screen.findByRole("button", {
      name: "Version: post",
    });

    fireEvent.pointerDown(button, { button: 0, ctrlKey: false });
    fireEvent.click(screen.getByRole("menuitem", { name: "post-1" }));
    expect(
      await screen.findByRole("heading", { name: "Earlier report" }),
    ).toBeVisible();
    expect(screen.getByText("kept in post-1")).toBeVisible();
    fireEvent.click(column().getByRole("link", { name: "Prompt" }));

    expect(
      await screen.findByRole("heading", { name: "Earlier prompt" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Version: post-1" }),
    ).toBeVisible();
    expect(screen.getByText("2 of 2")).toBeVisible();
  });

  it("says Not run for a part whose file is not there", async () => {
    serve(posts, { "post/out/report.md": "# Latest report\n" });
    show("&tab=post&part=prompt", parted);

    expect(
      await screen.findByText(
        "Not run: post/prompt.md: No such file or directory",
      ),
    ).toBeVisible();
  });

  it("says Not run, with no column, when the attempt has no version yet", async () => {
    serve([{ name: "post", versions: [] }], {});
    show("&tab=post", parted);

    expect(
      await screen.findByText("Not run: this attempt has no Post yet."),
    ).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Post" })).toBeNull();
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
  it("names each version by its pattern's label and version_title", async () => {
    serve(reviews, { "review-2/summary.md": "# Second\n" });
    show("&tab=reviews", sourced);
    const button = await screen.findByRole("button", {
      name: "Version: second",
    });

    fireEvent.pointerDown(button, { button: 0, ctrlKey: false });

    // A label with a value read, with none read, with no version_title; no
    // label, with a value read and with none.
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
      "second",
      "review-1",
      "Run 3 · merged",
      "Run 2 · runs/2",
      "Run 3, trial 1",
    ]);
  });

  it("shows a version's files from its path, with its own pattern's parts", async () => {
    serve(reviews, {
      "review-2/summary.md": "# Second\n",
      "runs/3/index.md": "# Merged\n",
      "runs/3/trial-1/summary.md": "# Trial summary\n",
    });
    show("&tab=reviews&part=summary", sourced);
    const choose = async (from: string, to: string) => {
      fireEvent.pointerDown(
        await screen.findByRole("button", { name: `Version: ${from}` }),
        { button: 0, ctrlKey: false },
      );
      fireEvent.click(screen.getByRole("menuitem", { name: to }));
    };
    const column = () =>
      within(screen.getByRole("navigation", { name: "Reviews" }));
    await screen.findByRole("heading", { name: "Second" });

    await choose("second", "Run 3 · merged");

    expect(
      await screen.findByRole("heading", { name: "Merged" }),
    ).toBeVisible();
    expect(screen.getByText("runs/3/index.md")).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Reviews" })).toBeNull();

    await choose("Run 3 · merged", "Run 3, trial 1");

    expect(
      await screen.findByRole("heading", { name: "Trial summary" }),
    ).toBeVisible();
    expect(screen.getByText("runs/3/trial-1/summary.md")).toBeVisible();
    expect(
      column()
        .getAllByRole("link")
        .map((l) => l.textContent),
    ).toEqual(["Summary", "Review"]);
    expect(screen.getByText("5 of 5")).toBeVisible();
  });
});
