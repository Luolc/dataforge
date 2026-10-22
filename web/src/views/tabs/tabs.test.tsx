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
import type { Project, TabVersions } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { AttemptPage } from "../attempt/AttemptPage";

const project: Project = {
  ...sampleProject,
  tabs: [
    {
      name: "notes",
      title: "Notes",
      versions: ["notes", "notes-*"],
      parts: [
        {
          name: "notes",
          title: "Notes",
          file: "out/notes.md",
          fields: [
            { title: "Verdict", format: "text" },
            { title: "Outcome", format: "text" },
            { title: "Spent", format: "usd" },
          ],
        },
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
        parts: [
          { name: "notes", fields: [field("n/a"), field("done"), field(0.25)] },
        ],
      },
      {
        dir: "notes-1",
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
    expect(value("Verdict")).toBe("null");
    expect(value("Outcome")).toBe(
      "Not run: notes/status.json: No such file or directory",
    );
    expect(value("Spent")).toBe("$1.50");
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
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
      await screen.findByText(
        "Not run: this attempt has no Notes yet (no directory matching notes or notes-*).",
      ),
    ).toBeVisible();
    expect(
      within(nav()).getByRole("link", { name: /^Notes/ }),
    ).toHaveTextContent("NotesNot run");
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

const parted: Project = {
  ...sampleProject,
  tabs: [
    {
      name: "post",
      title: "Post",
      versions: ["post", "post-*"],
      parts: [
        {
          name: "report",
          title: "Report",
          file: "out/report.md",
          fields: [{ title: "Verdict", format: "text" }],
        },
        { name: "prompt", title: "Prompt", file: "prompt.md", fields: [] },
      ],
    },
  ],
};

const posts: TabVersions[] = [
  {
    name: "post",
    versions: ["post", "post-1"].map((dir) => ({
      dir,
      parts: [
        { name: "report", fields: [field(`kept in ${dir}`)] },
        { name: "prompt", fields: [] },
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
    expect(screen.queryByRole("region", { name: /fields$/ })).toBeNull();
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
      await screen.findByText(
        "Not run: this attempt has no Post yet (no directory matching post or post-*).",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Post" })).toBeNull();
  });
});
