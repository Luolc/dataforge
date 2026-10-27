import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import older from "../../../fixtures/oldrec/attempts/older/task-0301/record.json";
import passed from "../../../fixtures/attempts/batch-1/task-0001/record.json";
import midway from "../../../fixtures/live-deliverables/attempts/task-0301/2026-03-04T101500Z/record.json";
import running from "../../../fixtures/attempts/batch-2/task-0005/record.json";
import { must, records, sampleProject } from "../../../fixtures/sample";
import type { AttemptRecord, Deliverable, Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { AttemptPage } from "./AttemptPage";
import { asShown } from "./declared";

// Declared in another order than the record's, with one the record lacks.
const project: Project = {
  ...sampleProject,
  deliverables: [
    { name: "review", kind: "report", title: "Review" },
    { name: "task", kind: "files", title: "Task" },
    { name: "pass_at_k", kind: "trials", title: "Pass@k" },
    { name: "notes", kind: "notes", title: "Notes" },
  ],
};

function show(
  body: unknown,
  at = "",
  shown: Project = project,
  live: unknown = { entries: [] },
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url.includes("/record?")
              ? body
              : url.includes("/live?")
                ? live
                : { entries: [] },
          ),
          { status: 200 },
        ),
      ),
    ),
  );
  render(
    <ProjectsContext value={{ state: "ok", data: [shown] }}>
      <MemoryRouter initialEntries={[`/${shown.id}/attempt?id=a/b${at}`]}>
        <Routes>
          <Route path=":project/attempt" element={<AttemptPage />} />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const links = async () =>
  within(
    await screen.findByRole("navigation", { name: "Attempt" }),
  ).getAllByRole("link");

/** Each tab as it shows: its title and its pill, without what only a reader hears. */
const tabs = async () =>
  (await links()).map((t) => {
    const shown = t.cloneNode(true);
    if (!(shown instanceof HTMLElement)) throw new Error("not an element");
    for (const heard of shown.querySelectorAll(".sr-only")) heard.remove();
    return shown.textContent;
  });

/** Each tab's count in full, as its pill's title says it. */
const counts = async () =>
  (await links()).map((t) => t.querySelector("[title]")?.getAttribute("title"));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the deliverables a project declares", () => {
  it("give a running attempt a tab each, in their order, counted Not run", async () => {
    show(running);

    expect(await tabs()).toEqual([
      "Runs2",
      "ReviewNot run",
      "TaskNot run",
      "Pass@kNot run",
      "NotesNot run",
    ]);
    expect(await counts()).toEqual([
      "2 runs",
      "Not run",
      "Not run",
      "Not run",
      "Not run",
    ]);
  });

  it("say in a tab that the running attempt has not reached them", async () => {
    show(running, "&tab=files");

    expect(
      await screen.findByText(
        /the attempt has not reached the state that makes it yet\./,
      ),
    ).toBeVisible();
  });

  it("are listed in the Summary, counted N of M", async () => {
    show(running);

    expect(
      (await screen.findByText("Deliverables", { selector: "dt" })).nextSibling,
    ).toHaveTextContent("0 of 4");
    const card = screen.getByRole("table");
    expect(within(card).getAllByRole("row")).toHaveLength(5);
    expect(
      within(card).getAllByText(/the attempt has not reached the state/),
    ).toHaveLength(4);
  });

  it("keep their order once the attempt ends, the record's entries shown", async () => {
    show(passed);

    expect(await tabs()).toEqual([
      "Runs6",
      "Review1",
      expect.stringMatching(/^Task\d*$/),
      "Pass@k2 / 4",
      "NotesNot run",
    ]);
    expect(await counts()).toEqual([
      "6 runs",
      "1 report",
      expect.stringMatching(/^Counting the files$|^\d+ files?$/),
      "2 of 4 solved",
      "Not run",
    ]);
    expect(
      (await screen.findByText("Deliverables", { selector: "dt" })).nextSibling,
    ).toHaveTextContent("3 of 4");
    expect(screen.getByText(/the attempt ended without it\./)).toBeVisible();
  });

  it("count a kind the viewer does not know by the ones reached", async () => {
    const notes = {
      name: "notes",
      kind: "notes",
      title: "Notes",
      state: "review",
      output: "notes",
      visit: 1,
      visit_index: 4,
      path: "notes/out/notes.md",
      reason: null,
    };
    show({ ...passed, deliverables: [...passed.deliverables, notes] });

    expect((await tabs()).at(-1)).toBe("Notes1");
    expect((await counts()).at(-1)).toBe("1 deliverable");
  });

  it("add no tab to a record with no deliverables key", async () => {
    show(older);

    expect(await tabs()).toEqual(["Runs4"]);
  });

  it("leave a project with no list in the record's order", async () => {
    show(passed, "", sampleProject);

    expect(await tabs()).toEqual([
      "Runs6",
      expect.stringMatching(/^Task\d*$/),
      "Pass@k2 / 4",
      "Review1",
    ]);
  });
});

describe("a tab of several deliverables of one kind", () => {
  // Two trials, the first declared reached only by some attempts.
  const twice: Project = {
    ...sampleProject,
    deliverables: [
      { name: "regrade", kind: "trials", title: "Regrade" },
      { name: "pass_at_k", kind: "trials", title: "Pass@k" },
    ],
  };
  // The record of an attempt that ended, with a regrade declared before its
  // pass@k: reached or not, and pass@k reached or not.
  const withRegrade = (
    regraded: boolean,
    solved = true,
    base = passed,
  ): unknown => {
    const record = structuredClone(base);
    const pass = record.deliverables.find((d) => d.name === "pass_at_k");
    if (pass === undefined) throw new Error("no pass_at_k");
    const unreached = (state: string) => ({
      state,
      visit: null,
      visit_index: null,
      path: null,
      reason: `${state} was never visited`,
    });
    const regrade = {
      ...pass,
      name: "regrade",
      title: "Regrade",
      state: "regrade",
      value: { ...pass.value, value: 1 },
      ...(regraded ? {} : unreached("regrade")),
    };
    const kept = { ...pass, ...(solved ? {} : unreached("solve")) };
    return {
      ...record,
      deliverables: record.deliverables.flatMap((d) =>
        d === pass ? [regrade, kept] : [d],
      ),
    };
  };

  it("is titled and counted by the first the attempt reached", async () => {
    show(withRegrade(false), "", twice);

    expect(await tabs()).toContain("Pass@k2 / 4");
  });

  it("is titled and counted by the first declared, when it was reached", async () => {
    show(withRegrade(true), "", twice);

    expect(await tabs()).toContain("Regrade1 / 4");
  });

  it("is titled by the first declared when none was reached", async () => {
    show(withRegrade(false, false), "", twice);

    expect(await tabs()).toContain("RegradeNot run");
  });

  it("opens on the deliverable it is titled by, the others in its menu", async () => {
    show(withRegrade(false), "&tab=trials", twice);

    const menu = await screen.findByRole("button", { name: /Pass@k/ });
    expect(menu).toHaveTextContent(/^Pass@k$/);
    expect(await screen.findByText(/2 \/ 4 solved/)).toBeVisible();
    expect(screen.queryByText("the attempt never reached Regrade.")).toBeNull();
  });

  it("opens on the first declared when none was reached", async () => {
    show(withRegrade(false, false), "&tab=trials", twice);

    expect(
      await screen.findByText("Not run: the attempt never reached Regrade."),
    ).toBeVisible();
  });

  // As twice, with pass@k marked as the one to name the tab by while none
  // is reached, and the kind's tab titled by the project.
  const marked: Project = {
    ...sampleProject,
    deliverables: [
      { name: "regrade", kind: "trials", title: "Regrade" },
      { name: "pass_at_k", kind: "trials", title: "Pass@k", default: true },
    ],
  };

  it("is titled by the one marked default when none was reached", async () => {
    show(withRegrade(false, false), "", marked);

    expect(await tabs()).toContain("Pass@kNot run");
  });

  it("opens on the one marked default when none was reached", async () => {
    show(withRegrade(false, false), "&tab=trials", marked);

    const menu = await screen.findByRole("button", { name: /Pass@k/ });
    expect(menu).toHaveTextContent(/^Pass@k$/);
    expect(screen.queryByText("the attempt never reached Regrade.")).toBeNull();
  });

  it("is titled by the first reached over the one marked default", async () => {
    show(withRegrade(true, false), "", marked);

    expect(await tabs()).toContain("Regrade1 / 4");
  });

  // As twice, with pass@k to lead its kind when its visit's data has
  // settings ["explicit"], and both reached from a visit of that data.
  const told = (equals: unknown): Project => ({
    ...sampleProject,
    deliverables: [
      { name: "regrade", kind: "trials", title: "Regrade" },
      {
        name: "pass_at_k",
        kind: "trials",
        title: "Pass@k",
        default: { field: ["settings"], equals },
      },
    ],
  });
  const withSettings = (settings: string[]): unknown => {
    const record = structuredClone(passed);
    const pass = record.deliverables.find((d) => d.name === "pass_at_k");
    const visit = must(record.visits[must(pass?.visit_index)]);
    Object.assign(visit.data, { settings });
    return withRegrade(true, true, record);
  };

  it("is titled by the one whose condition holds over the first reached", async () => {
    show(withSettings(["explicit"]), "", told(["explicit"]));

    expect(await tabs()).toContain("Pass@k2 / 4");
  });

  it("opens on the one whose condition holds and lists it first", async () => {
    show(withSettings(["explicit"]), "&tab=trials", told(["explicit"]));

    const menu = await screen.findByRole("button", { name: /Pass@k/ });
    expect(menu).toHaveTextContent(/^Pass@k$/);
    expect(await screen.findByText(/2 \/ 4 solved/)).toBeVisible();
    fireEvent.pointerDown(menu, { button: 0, ctrlKey: false });
    const items = screen.getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual([
      expect.stringMatching(/^Pass@k/),
      expect.stringMatching(/^Regrade/),
    ]);
  });

  it("is titled by the first reached when the condition does not hold", async () => {
    show(withSettings(["implicit", "explicit"]), "", told(["explicit"]));

    expect(await tabs()).toContain("Regrade1 / 4");
  });

  it("titled by the project names the one its count is of", async () => {
    show(withRegrade(true), "", {
      ...marked,
      kinds: { trials: { title: "Trials" } },
    });

    expect(await tabs()).toContain("Trials1 / 4");
    expect(await counts()).toContain("Regrade: 1 of 4 solved");
  });

  // Two reports, the first declared reached only by some attempts: the rule
  // holds for every kind, a stacked one too.
  const reviews: Project = {
    ...sampleProject,
    deliverables: [
      { name: "tightened", kind: "report", title: "Tightened review" },
      { name: "review", kind: "report", title: "Review" },
    ],
  };
  const withTightened = (reached: boolean, base = passed): unknown => {
    const record = structuredClone(base);
    const review = record.deliverables.find((d) => d.name === "review");
    if (review === undefined) throw new Error("no review");
    const tightened = {
      ...review,
      name: "tightened",
      title: "Tightened review",
      ...(reached
        ? {}
        : {
            state: "tighten",
            visit: null,
            visit_index: null,
            path: null,
            reason: "tighten was never visited",
          }),
    };
    return {
      ...record,
      deliverables: record.deliverables.flatMap((d) =>
        d === review ? [tightened, review] : [d],
      ),
    };
  };

  it("of reports is titled by the first reached, counting each reached", async () => {
    show(withTightened(false), "", reviews);

    expect(await tabs()).toContain("Review1");
  });

  it("of reports is titled by the first declared, when it was reached", async () => {
    show(withTightened(true), "", reviews);

    expect(await tabs()).toContain("Tightened review2");
  });

  it("of reports titled by the project counts every report reached", async () => {
    show(withTightened(true), "", {
      ...reviews,
      kinds: { report: { title: "Reviews" } },
    });

    expect(await tabs()).toContain("Reviews2");
    expect(await counts()).toContain("2 reports");
  });

  // The entries of the reports' column, and the title of each report shown.
  const column = () =>
    within(
      screen.getByRole("navigation", { name: /^(Review|Tightened review)$/ }),
    )
      .getAllByRole("link")
      .map((l) => l.textContent);
  const shownReports = () =>
    screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);

  it("of reports lists each in a column, the reached ones first, and shows the first", async () => {
    show(withTightened(false), "&tab=report", reviews);

    await waitFor(() => {
      expect(column()).toEqual(["Review", "Tightened reviewNot run"]);
    });
    expect(shownReports()).toEqual(["Review"]);
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    fireEvent.click(screen.getByRole("link", { name: /^Tightened review/ }));

    expect(
      await screen.findByText("Not run: the attempt never reached Tighten."),
    ).toBeVisible();
    expect(shownReports()).toEqual(["Tightened review"]);
  });

  it("opens the tab of a deliverable's kind from a link made with its name", async () => {
    show(withTightened(false), "&tab=tightened", reviews);

    await waitFor(() => {
      expect(column()).toEqual(["Review", "Tightened reviewNot run"]);
    });
    expect(shownReports()).toEqual(["Review"]);
    expect(screen.queryByText(/has no deliverable of kind/)).toBeNull();
  });

  it("of reports lists first and shows the one whose condition holds", async () => {
    const record = structuredClone(passed);
    const review = record.deliverables.find((d) => d.name === "review");
    Object.assign(must(record.visits[must(review?.visit_index)]).data, {
      verdict: "pass",
    });
    show(withTightened(true, record), "&tab=report", {
      ...sampleProject,
      deliverables: [
        { name: "tightened", kind: "report", title: "Tightened review" },
        {
          name: "review",
          kind: "report",
          title: "Review",
          default: { field: ["verdict"], equals: "pass" },
        },
      ],
    });

    await waitFor(() => {
      expect(column()).toEqual(["Review", "Tightened review"]);
    });
    expect(shownReports()).toEqual(["Review"]);
  });

  it("of reports keeps the declared order when all were reached", async () => {
    show(withTightened(true), "&tab=report", reviews);

    await waitFor(() => {
      expect(column()).toEqual(["Tightened review", "Review"]);
    });
    expect(shownReports()).toEqual(["Tightened review"]);
  });
});

describe("a running attempt's record", () => {
  // As the engine writes it while its pass@4 runs: the task and the reviews
  // made, the trials started.
  const live: Project = {
    ...sampleProject,
    deliverables: [
      { name: "task", kind: "files", title: "Task" },
      { name: "pass_at_k", kind: "trials", title: "pass@4" },
      { name: "reviews", kind: "report", title: "Reviews" },
    ],
  };

  it("counts on its tabs what it has made", async () => {
    show(midway, "", live);

    expect(await tabs()).toEqual([
      "Runs3",
      expect.stringMatching(/^Task\d*$/),
      "pass@4— / 4",
      "Reviews1",
    ]);
  });

  it("lists what it has made in the Summary, none as not run", async () => {
    show(midway, "", live);

    expect(
      (await screen.findByText("Deliverables", { selector: "dt" })).nextSibling,
    ).toHaveTextContent(/^3$/);
    const card = screen.getByRole("table");
    expect(within(card).getAllByRole("row")).toHaveLength(4);
    expect(within(card).queryByText(/Not run/)).toBeNull();
    expect(within(card).getByText("— / 4 solved")).toBeVisible();
    expect(within(card).getByText("report.md")).toBeVisible();
  });

  it.each([
    ["running", 2],
    ["may have stopped", 0],
  ] as const)(
    "hands its trials tab whether it runs: %s",
    async (word, spinners) => {
      show(midway, "&tab=trials", live, {
        live: word,
        held: word === "running",
        current: null,
      });

      const list = await screen.findByRole("navigation", { name: "Trials" });
      await waitFor(() => {
        expect(list.querySelectorAll(".lucide-loader-circle")).toHaveLength(
          spinners,
        );
      });
    },
  );

  it("counts only what it reached, of all", async () => {
    const early = structuredClone(midway);
    const reviews = early.deliverables.find((d) => d.name === "reviews");
    if (reviews === undefined) throw new Error("no reviews");
    Object.assign(reviews, {
      state: "tighten",
      visit: null,
      visit_index: null,
      path: null,
      reason: "tighten was never visited",
    });
    show(early, "", live);

    expect(await tabs()).toContain("ReviewsNot run");
    expect(
      (await screen.findByText("Deliverables", { selector: "dt" })).nextSibling,
    ).toHaveTextContent("2 of 3");
    expect(
      screen.getByText("Not run: the attempt has not reached Tighten yet."),
    ).toBeVisible();
  });
});

describe("a deliverable's title", () => {
  const renamed: Project = {
    ...sampleProject,
    deliverables: [
      { name: "task", kind: "files", title: "Task" },
      { name: "pass_at_k", kind: "trials", title: "Pass@K" },
      { name: "reviews", kind: "report", title: "Quality reviews" },
    ],
  };

  it("is the project's, where the record titled it otherwise", async () => {
    show(midway, "", renamed);

    expect(await tabs()).toEqual([
      "Runs3",
      expect.stringMatching(/^Task\d*$/),
      "Pass@K— / 4",
      "Quality reviews1",
    ]);
    expect(
      within(screen.getByRole("table")).getByText("Quality reviews"),
    ).toBeVisible();
  });

  it("is the record's, where the project does not list the deliverable", async () => {
    show(midway, "", {
      ...renamed,
      deliverables: [renamed.deliverables?.[0] ?? never()],
    });

    expect(await tabs()).toEqual([
      "Runs3",
      expect.stringMatching(/^Task\d*$/),
      "pass@4— / 4",
      "Reviews1",
    ]);
  });
});

function never(): never {
  throw new Error("no deliverable");
}

describe("why a deliverable has no path, in the viewer's words", () => {
  // passed: author, check, author, check, review, then solve (Pass@k).
  const ended = records.passed;
  const running: AttemptRecord = { ...ended, status: null };
  const entry = (over: Partial<Deliverable>): Deliverable => ({
    ...must(ended.deliverables?.find((d) => d.name === "pass_at_k")),
    visit: null,
    visit_index: null,
    path: null,
    reason: "the engine's own words",
    ...over,
  });
  const why = (record: AttemptRecord, item: Deliverable) =>
    must(
      asShown(sampleProject, { ...record, deliverables: [item] })
        .deliverables?.[0],
    ).reason;
  const before = (n: number, outcome: string | null) => ({
    ...running,
    visits: [
      ...ended.visits.slice(0, n),
      { ...must(ended.visits[n]), outcome },
    ],
  });

  it("names the state by its title, and says whether the attempt runs on", () => {
    const item = entry({ state: "regrade" });
    const regraded = { ...sampleProject.states, regrade: { title: "Regrade" } };
    const project = { ...sampleProject, states: regraded };
    const shown = (record: AttemptRecord) =>
      must(
        asShown(project, { ...record, deliverables: [item] }).deliverables,
      )[0]?.reason;

    expect(shown(ended)).toBe("the attempt never reached Regrade.");
    expect(shown(running)).toBe("the attempt has not reached Regrade yet.");
  });

  it("says a state in its first run has not finished one", () => {
    expect(why(before(5, null), entry({}))).toBe(
      "Pass@k has not finished a run yet.",
    );
    // Its process stopped and the attempt ended with a status meanwhile.
    expect(why({ ...before(5, null), status: "exhausted" }, entry({}))).toBe(
      "Pass@k never finished a run.",
    );
  });

  it("names the run of a state that did not finish, and how it ended", () => {
    // passed's Pass@k, run 6, raised or was interrupted.
    for (const [outcome, how] of [
      ["raised", "Exception"],
      ["interrupted", "Run interrupted"],
    ] as const)
      expect(
        why({ ...before(5, outcome), status: null }, entry({ state: "solve" })),
      ).toBe(`Pass@k · run 6 did not finish (${how}).`);
  });

  it("says a state ran only after the run the attempt kept", () => {
    const kept = { ...ended, kept_visit: 1 };

    expect(why(kept, entry({ state: "review" }))).toBe(
      "Review ran only after run 2, the run this attempt kept.",
    );
  });

  it("says a state's runs do not include the one the deliverable takes", () => {
    expect(why(ended, entry({}))).toBe(
      "Pass@k's 1 run does not include the one this takes.",
    );
    expect(why(running, entry({ state: "author" }))).toBe(
      "Author's 2 runs do not include the one this takes yet.",
    );
  });

  it("says which run wrote no output the deliverable takes", () => {
    const item = entry({
      name: "task",
      kind: "files",
      state: "author",
      output: "task",
      visit: 2,
      visit_index: 2,
    });

    expect(why(ended, item)).toBe("Author · run 3 wrote no task.");
  });

  it("keeps the record's reason of one that has a path", () => {
    const item = entry({ path: "artifacts/solve/1", visit_index: 5 });

    expect(why(ended, { ...item, reason: null })).toBeNull();
  });
});
