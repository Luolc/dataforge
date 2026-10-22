import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import older from "../../../fixtures/oldrec/attempts/older/task-0301/record.json";
import passed from "../../../fixtures/attempts/batch-1/task-0001/record.json";
import midway from "../../../fixtures/live-deliverables/attempts/task-0301/2026-03-04T101500Z/record.json";
import running from "../../../fixtures/attempts/batch-2/task-0005/record.json";
import { sampleProject } from "../../../fixtures/sample";
import type { Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { AttemptPage } from "./AttemptPage";

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

const tabs = async () =>
  within(await screen.findByRole("navigation", { name: "Attempt" }))
    .getAllByRole("link")
    .map((t) => t.textContent);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the deliverables a project declares", () => {
  it("give a running attempt a tab each, in their order, counted —", async () => {
    show(running);

    expect(await tabs()).toEqual([
      "Runs2",
      "Review—",
      "Task—",
      "Pass@k—",
      "Notes—",
    ]);
  });

  it("say in a tab that the running attempt has not reached them", async () => {
    show(running, "&tab=files");

    expect(
      await screen.findByText(
        /This attempt has not reached the state that makes it\./,
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
      within(card).getAllByText(/This attempt has not reached the state/),
    ).toHaveLength(4);
  });

  it("keep their order once the attempt ends, the record's entries shown", async () => {
    show(passed);

    expect(await tabs()).toEqual([
      "Runs6",
      "Review1",
      expect.stringMatching(/^Task\d*$/),
      "Pass@k2 / 4",
      "Notes—",
    ]);
    expect(
      (await screen.findByText("Deliverables", { selector: "dt" })).nextSibling,
    ).toHaveTextContent("3 of 4");
    expect(screen.getByText(/This attempt ended without it\./)).toBeVisible();
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
  const withRegrade = (regraded: boolean, solved = true): unknown => {
    const record = structuredClone(passed);
    const pass = record.deliverables.find((d) => d.name === "pass_at_k");
    if (pass === undefined) throw new Error("no pass_at_k");
    const unreached = (state: string) => ({
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
    expect(screen.queryByText("regrade was never visited")).toBeNull();
  });

  it("opens on the first declared when none was reached", async () => {
    show(withRegrade(false, false), "&tab=trials", twice);

    expect(await screen.findByText("regrade was never visited")).toBeVisible();
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
  const withTightened = (reached: boolean): unknown => {
    const record = structuredClone(passed);
    const review = record.deliverables.find((d) => d.name === "review");
    if (review === undefined) throw new Error("no review");
    const tightened = {
      ...review,
      name: "tightened",
      title: "Tightened review",
      ...(reached
        ? {}
        : {
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
      visit: null,
      visit_index: null,
      path: null,
      reason: "review was never visited",
    });
    show(early, "", live);

    expect(await tabs()).toContain("ReviewsNot run");
    expect(
      (await screen.findByText("Deliverables", { selector: "dt" })).nextSibling,
    ).toHaveTextContent("2 of 3");
    expect(screen.getByText("Not run: review was never visited")).toBeVisible();
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
