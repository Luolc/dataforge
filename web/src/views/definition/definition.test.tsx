import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import snapshot from "../../../fixtures/attempts/batch-1/task-0001/definition.json";
import { records } from "../../../fixtures/sample";
import type { AttemptRow, Project } from "../../api/types";
import { AttemptPage } from "../attempt/AttemptPage";
import { HomePage } from "../HomePage";
import { ProjectsContext } from "../../shell/projects";
import { row } from "../home/rows.test-data";
import { DefinitionPage } from "./DefinitionPage";
import { SCAN_LIMIT } from "./load";
import { readDefinition } from "./types";

const project: Project = {
  id: "sample",
  name: "Sample dataset",
  color: "#e5a54b",
  states: { author: { title: "Author" }, review: { title: "Review" } },
  statuses: {},
  item: { depth: 1, title: "Task" },
  columns: [],
  session: { thinking: "collapsed" },
  definition: "definition.json",
};

const text = JSON.stringify(snapshot);

/**
 * Stub fetch: the listing gives rows, and the files route each attempt's
 * file from files, a 404 for one it does not hold.
 */
function serve(rows: AttemptRow[], files: Record<string, string>) {
  const fetch = vi.fn((input: string) => {
    const url = new URL(input, "http://viz");
    if (url.pathname.endsWith("/attempts"))
      return Promise.resolve(Response.json(rows));
    const attempt = url.searchParams.get("attempt") ?? "";
    const file = files[attempt];
    return Promise.resolve(
      file === undefined
        ? Response.json(
            { error: "not found", reason: "no such file" },
            { status: 404 },
          )
        : new Response(file),
    );
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

/** The attempts files read, in order. */
const filesRead = (fetch: ReturnType<typeof serve>) =>
  fetch.mock.calls
    .map(([input]) => new URL(input, "http://viz"))
    .filter((u) => u.pathname.endsWith("/files"))
    .map((u) => u.searchParams.get("attempt"));

function show(shown: Project = project, at = "") {
  render(
    <ProjectsContext value={{ state: "ok", data: [shown] }}>
      <MemoryRouter initialEntries={[`/${shown.id}/definition${at}`]}>
        <Routes>
          <Route path=":project/definition" element={<DefinitionPage />} />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const hour = (h: number) =>
  `2026-03-02T${h.toString().padStart(2, "0")}:00:00+00:00`;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reading a snapshot", () => {
  it("takes the fixture's snapshot, an entry of another kind included", () => {
    const read = readDefinition(text);
    expect(read.shape).toBe("definition");
    if (read.shape !== "definition") return;
    const review = read.definition.stages.find((s) => s.state === "review");
    expect(review?.entries.map((e) => e.kind)).toEqual([
      "rubric",
      "rubric",
      "checklist",
    ]);
  });

  it("names the first key that does not fit", () => {
    const broken = structuredClone(snapshot) as {
      stages: { entries: Record<string, unknown>[] }[];
    };
    delete broken.stages[0]?.entries[1]?.["text"];
    expect(readDefinition(JSON.stringify(broken))).toMatchObject({
      shape: "other",
      why: "it has no stages.0.entries.1.text",
    });
    expect(readDefinition("{")).toMatchObject({
      shape: "other",
      why: "it is not JSON",
    });
  });
});

describe("an older snapshot whose rubric ranks its criteria", () => {
  it("loads, and the page reads nothing of the rank", async () => {
    // Each criterion of tests.toml with the severity older rubrics had.
    const older = text.replaceAll('"id":"T', '"severity":"error","id":"T');
    expect(older).not.toBe(text);
    serve([row("t/1")], { "t/1": older });
    show(project, "?defn=review");

    const tests = await screen.findByRole("region", { name: "tests.toml" });
    expect(within(tests).getByText("5 criteria")).toBeVisible();
    expect(
      within(tests)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["Open", "ID and question"]);
    expect(within(tests).queryByText(/error/i)).toBeNull();
  });
});

describe("a rubric's criteria", () => {
  it("show each ID over its whole question, wrapped, and open to the rest", async () => {
    // A long ID and question, and many reads, of no break but at its end.
    const id = "every_widget_is_painted_blue_on_all_sides";
    const question = `${"Does the work hold up? ".repeat(20)}Answer.`;
    const reads = Array.from(
      { length: 22 },
      (_, i) => `input_block_${String(i)}`,
    );
    const long = text
      .replace('"id":"T01"', `"id":"${id}"`)
      .replace(
        /"question":"Does the work meet made-up rule 1\?[^"]*"/,
        `"question":"${question}"`,
      )
      .replace(
        '"reads":["tests","solution"]',
        `"reads":${JSON.stringify(reads)}`,
      );
    expect(long).toContain(question);
    expect(long).toContain(reads[21]);
    serve([row("t/1")], { "t/1": long });
    show(project, "?defn=review");

    const tests = await screen.findByRole("region", { name: "tests.toml" });
    const cell = within(tests).getByText(id).closest("td");
    expect(cell).toHaveTextContent(`${id}${question}`);
    expect(cell).toHaveClass("wrap-anywhere");
    expect(cell).not.toHaveClass("truncate");
    expect(cell?.parentElement?.children).toHaveLength(2);
    const reading = within(tests)
      .getByText(reads[21] ?? "")
      .closest("p");
    expect(reading).toHaveClass("min-w-0", "wrap-anywhere");

    const opener = within(tests).getByRole("button", {
      name: `${id}: check, pass if and fail if`,
    });
    expect(opener).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(opener);
    const detail = document.getElementById(
      opener.getAttribute("aria-controls") ?? "",
    );
    expect(detail).toBeVisible();
    expect(detail).toHaveTextContent("Rule 1 holds everywhere it applies.");
    expect(detail?.querySelector("td:last-child")).toHaveClass("wrap-anywhere");
  });

  it("put the card's aside under its title, whole, when the two do not fit", async () => {
    serve([row("t/1")], { "t/1": text });
    show(project, "?defn=review");

    const brief = await screen.findByRole("region", { name: "brief.toml" });
    const header = within(brief).getByRole("heading", {
      name: "brief.toml",
    }).parentElement;
    expect(header).toHaveClass("flex-wrap");
    expect(within(brief).getByText(/3 criteria/)).toHaveClass(
      "ml-auto",
      "flex-wrap",
    );
  });
});

describe("the Definition page", () => {
  it("shows the newest attempt's snapshot that has one, and from where", async () => {
    const fetch = serve(
      [
        row("t/1", { started_at: hour(9) }),
        row("t/3", { started_at: hour(11) }),
        row("t/2", { started_at: hour(10) }),
      ],
      { "t/2": text },
    );
    show();

    const from = await screen.findByRole("link", { name: "t/2" });
    expect(from).toHaveAttribute("href", "/sample/attempt?id=t%2F2");
    expect(filesRead(fetch)).toEqual(["t/3", "t/2"]);
    expect(screen.getByRole("link", { name: /Author/ })).toBeVisible();
  });

  it("counts each stage's entries with their unit, not as a bare number", async () => {
    serve([row("t/1")], { "t/1": text });
    show();

    expect(
      await screen.findByRole("link", { name: /^Author/ }),
    ).toHaveTextContent(/^Author2 entries$/);
    expect(screen.getByRole("link", { name: /^Review/ })).toHaveTextContent(
      /^Review3 entries$/,
    );
    expect(screen.getByRole("link", { name: /^check/ })).toHaveTextContent(
      /^check1 entry$/,
    );
  });

  it("cuts a long version value and shows it whole in its label", async () => {
    serve([row("t/1")], { "t/1": text });
    show();

    const commit = await screen.findByLabelText(snapshot.version.commit);
    expect(commit).toHaveTextContent(/^0123456789ab$/);
    expect(commit).toHaveAttribute("tabindex", "0");
    expect(screen.getByText("Dirty")).toBeVisible();
  });

  it("reads at most SCAN_LIMIT attempts, and says how many", async () => {
    const rows = Array.from({ length: SCAN_LIMIT + 5 }, (_, i) =>
      row(`t/${i.toString()}`),
    );
    const fetch = serve(rows, {});
    show();

    expect(
      await screen.findByText(
        `None of the project's newest ${SCAN_LIMIT.toString()} attempts recorded its definition.`,
      ),
    ).toBeVisible();
    expect(filesRead(fetch)).toHaveLength(SCAN_LIMIT);
    expect(screen.getByText("Not run")).toBeVisible();
  });

  it("says when none of all the project's attempts has one", async () => {
    serve([row("t/1")], {});
    show();

    expect(
      await screen.findByText(
        "None of the project's attempts recorded its definition.",
      ),
    ).toBeVisible();
  });

  it("shows a snapshot not of the shape as text, with why", async () => {
    serve([row("t/1")], { "t/1": '{"version": 1}' });
    show();

    expect(
      await screen.findByText(
        /^This definition is not in a shape the viewer knows \(version/,
      ),
    ).toBeVisible();
    expect(screen.getByText('{"version": 1}')).toBeVisible();
  });

  it("says so for a project that names no definition, and reads nothing", () => {
    const fetch = serve([row("t/1")], {});
    show({ ...project, definition: null });

    expect(screen.getByText("This project names no definition.")).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("a template's text", () => {
  it("keeps raw HTML as text and runs nothing, its variables drawn in place", async () => {
    serve([row("t/1")], { "t/1": text });
    show();

    const card = await screen.findByRole("region", { name: "author.md" });
    expect(card.querySelector("b, script")).toBeNull();
    expect(within(card).getByText(/<b>raw HTML<\/b> stays text/)).toBeVisible();
    expect(within(card).getByRole("button", { name: "repo" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    // $$ is a dollar sign to string.Template, so $$task is no variable.
    expect(within(card).getByText(/so does \$\$task, a dollar/)).toBeVisible();
    expect(within(card).getAllByRole("button", { name: /^task/ })).toHaveLength(
      1,
    );
    fireEvent.click(within(card).getByRole("button", { name: "Code" }));
    expect(
      within(card)
        .getAllByText("${repo}")
        .map((e) => e.tagName),
    ).toContain("MARK");
  });
});

/** Stub fetch for an attempt's page: its record, /live, and its snapshot. */
function serveAttempt(file: string | null) {
  const fetch = vi.fn((input: string) => {
    const url = new URL(input, "http://viz");
    if (url.pathname.endsWith("/record"))
      return Promise.resolve(Response.json(records.passed));
    if (url.pathname.endsWith("/live"))
      return Promise.resolve(
        Response.json({ live: null, held: null, current: null }),
      );
    return Promise.resolve(
      file === null || url.searchParams.get("path") !== "definition.json"
        ? Response.json(
            { error: "not found", reason: "no such file" },
            { status: 404 },
          )
        : new Response(file),
    );
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function showAttempt(shown: Project = project, at = "") {
  render(
    <ProjectsContext value={{ state: "ok", data: [shown] }}>
      <MemoryRouter
        initialEntries={[`/${shown.id}/attempt?id=batch-1/task-0001${at}`]}
      >
        <Routes>
          <Route path=":project/attempt" element={<AttemptPage />} />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const column = async () =>
  within(await screen.findByRole("navigation", { name: "Runs" }));

describe("an attempt's definition in the Runs column", () => {
  it("follows Summary and leads the runs, with no tab of its own", async () => {
    serveAttempt(text);
    showAttempt();

    const workflow = await (
      await column()
    ).findByRole("link", {
      name: "Workflow file",
    });
    const summary = (await column()).getByRole("link", { name: "Summary" });
    expect(
      summary.compareDocumentPosition(workflow) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const list = (await column()).getByRole("region", { name: "Runs list" });
    expect(
      workflow.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      (await column()).getByRole("link", { name: /^Review/ }),
    ).toHaveAttribute(
      "href",
      "/sample/attempt?id=batch-1%2Ftask-0001&tab=runs&defn=review",
    );
    expect(
      (await column()).getByRole("region", { name: "Runs list" }),
    ).toHaveAttribute("tabindex", "0");
    const tabs = within(screen.getByRole("navigation", { name: "Attempt" }));
    expect(tabs.queryByRole("link", { name: /Definition/ })).toBeNull();
  });

  it("shows a stage with the attempt's values, and names it in the breadcrumb", async () => {
    serveAttempt(text);
    showAttempt(project, "&tab=runs&defn=author");

    const card = await screen.findByRole("region", { name: "author.md" });
    expect(
      within(card).getByRole("button", { name: "repo: example/tinyparse" }),
    ).toBeVisible();
    expect(
      within(card).getByRole("button", {
        name: "feedback: differs each round",
      }),
    ).toBeVisible();
    expect(
      within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText(
        "Author · prompts and rubrics",
      ),
    ).toBeVisible();
    // A template puts a value in place in code too: in a code span and in a
    // fenced block, the variable is a block with the attempt's value.
    const commits = within(card).getAllByRole("button", {
      name: "commit: abc1234",
    });
    expect(
      commits.map((b) => b.closest("code")?.parentElement?.tagName),
    ).toEqual(["P", "PRE"]);
    const rendered = card.querySelector(".md");
    expect(rendered).not.toBeNull();
    expect(rendered?.textContent).not.toContain("${commit}");
  });

  it("draws a Jinja variable in a code span as a block", async () => {
    serveAttempt(text);
    showAttempt(project, "&tab=runs&defn=solve");

    const card = await screen.findByRole("region", { name: "solve.md.j2" });
    const workdir = within(card).getByRole("button", { name: "workdir: work" });
    expect(workdir.closest("code")?.parentElement?.tagName).toBe("P");
    expect(card.querySelector(".md")?.textContent).not.toContain("{{");
  });

  it("opens a variable's box over the page, named only while it is open", async () => {
    serveAttempt(text);
    showAttempt(project, "&tab=runs&defn=author");

    const card = await screen.findByRole("region", { name: "author.md" });
    const repo = within(card).getByRole("button", {
      name: "repo: example/tinyparse",
    });
    expect(repo).toHaveAttribute("aria-expanded", "false");
    expect(repo).not.toHaveAttribute("aria-controls");
    fireEvent.click(repo);
    const box = screen.getByRole("note");
    expect(repo).toHaveAttribute("aria-expanded", "true");
    expect(repo).toHaveAttribute("aria-controls", box.id);
    // Outside the card, so that the card's edge does not cut it.
    expect(card).not.toContainElement(box);
    expect(box).toHaveTextContent("In this attempt: example/tinyparse");
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("note")).toBeNull();
    });
    expect(repo).not.toHaveAttribute("aria-controls");
  });

  it("keeps a variable's block inside its card, its value cut and shown whole in the box", async () => {
    // A value of no space, past what the block shows.
    const value = "x".repeat(300);
    serveAttempt(
      text.replace('"value":"example/tinyparse"', `"value":"${value}"`),
    );
    showAttempt(project, "&tab=runs&defn=author");

    const card = await screen.findByRole("region", { name: "author.md" });
    const repo = within(card).getByRole("button", { name: `repo: ${value}` });
    expect(repo).toHaveClass("inline-flex", "max-w-full");
    expect(within(repo).getByText("repo")).toHaveClass(
      "min-w-0",
      "wrap-anywhere",
    );
    expect(within(repo).getByText(value)).toHaveClass("min-w-0", "truncate");
    fireEvent.click(repo);
    expect(screen.getByRole("note")).toHaveTextContent(
      `In this attempt: ${value}`,
    );
  });

  it("shows the definition over a run when the link names both", async () => {
    serveAttempt(text);
    showAttempt(project, "&tab=runs&run=1&defn=workflow");

    expect(
      await screen.findByRole("region", { name: "sample/workflow.yaml" }),
    ).toBeVisible();
    const crumbs = within(
      screen.getByRole("navigation", { name: "Breadcrumb" }),
    ).getAllByRole("listitem");
    expect(crumbs.map((c) => c.textContent)).toEqual([
      "Projects",
      "Sample dataset",
      "batch-1/task-0001",
      "Workflow file",
    ]);
    const runs = await column();
    expect(runs.getByRole("link", { name: "Workflow file" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      runs
        .getAllByRole("link")
        .filter((l) => l.getAttribute("aria-current") === "page"),
    ).toHaveLength(1);
  });

  it("says an attempt with no snapshot has none, as not run", async () => {
    serveAttempt(null);
    showAttempt();

    expect(await (await column()).findByText("Not recorded")).toBeVisible();
    expect(
      (await column()).queryByRole("link", { name: "Workflow file" }),
    ).toBeNull();
  });

  it("reads no snapshot for a project that names none", async () => {
    const fetch = serveAttempt(text);
    showAttempt({ ...project, definition: null });

    await (await column()).findByRole("link", { name: "Summary" });
    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
    expect(
      fetch.mock.calls.filter(([input]) => input.includes("definition.json")),
    ).toEqual([]);
    expect(screen.queryByText("Definition")).toBeNull();
  });
});

describe("the home page's Definition link", () => {
  function showHome(shown: Project) {
    serve([row("t/1")], {});
    render(
      <ProjectsContext value={{ state: "ok", data: [shown] }}>
        <MemoryRouter initialEntries={[`/${shown.id}`]}>
          <Routes>
            <Route path=":project" element={<HomePage />} />
          </Routes>
        </MemoryRouter>
      </ProjectsContext>,
    );
  }

  it("opens the Definition page beside Refresh", async () => {
    showHome(project);

    await screen.findByRole("button", { name: "Refresh" });
    expect(screen.getByRole("link", { name: "Definition" })).toHaveAttribute(
      "href",
      "/sample/definition",
    );
  });

  it("is not there for a project that names no definition", async () => {
    showHome({ ...project, definition: null });

    await screen.findByRole("button", { name: "Refresh" });
    expect(screen.queryByRole("link", { name: "Definition" })).toBeNull();
  });
});
