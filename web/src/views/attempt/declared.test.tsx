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
        expect(
          within(list).queryAllByRole("img", { name: "Running" }),
        ).toHaveLength(spinners);
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
