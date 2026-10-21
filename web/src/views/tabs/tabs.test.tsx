import { fireEvent, render, screen, within } from "@testing-library/react";
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
      file: "out/notes.md",
      fields: [
        { title: "Verdict", format: "text" },
        { title: "Outcome", format: "text" },
        { title: "Spent", format: "usd" },
      ],
    },
  ],
};

const field = (value: unknown) => ({ value, missing: null });
const missing = (why: string) => ({ value: null, missing: why });

const three: TabVersions[] = [
  {
    name: "notes",
    versions: [
      {
        dir: "notes",
        fields: [
          field(null),
          missing("notes/status.json: No such file or directory"),
          field(1.5),
        ],
      },
      {
        dir: "notes-2",
        fields: [field("n/a"), field("done"), field(0.25)],
      },
      { dir: "notes-1", fields: [field("x"), field("y"), field(0)] },
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

function show(at = "&tab=notes") {
  render(
    <ProjectsContext value={{ state: "ok", data: [project] }}>
      <MemoryRouter
        initialEntries={[`/${project.id}/attempt?id=older/task-0301${at}`]}
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
      "notes/status.json: No such file or directory",
    );
    expect(value("Spent")).toBe("$1.50");
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
