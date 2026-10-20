import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import older from "../../../fixtures/oldrec/attempts/older/task-0301/record.json";
import passed from "../../../fixtures/attempts/batch-1/task-0001/record.json";
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

function show(body: unknown, at = "", shown: Project = project) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(url.includes("/record?") ? body : { entries: [] }),
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
