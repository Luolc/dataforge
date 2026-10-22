import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import regrade from "../../../fixtures/regrade/attempts/regrade/record.json";
import solved from "../../../fixtures/regrade/attempts/regrade/artifacts/solve/1/trial-1/trace.jsonl?raw";
import { sampleProject } from "../../../fixtures/sample";
import type { Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { AttemptPage } from "../attempt/AttemptPage";

// As web/fixtures/regrade/project.yaml gives it.
const NO_TRACE =
  "A regrade runs no agent: it applies another trial's diff and runs the task's tests on it.";
const project: Project = {
  ...sampleProject,
  id: "regrade",
  states: { solve: { title: "Pass@k" }, regrade: { title: "Regrade" } },
  deliverables: [
    { name: "pass_at_k", kind: "trials", title: "Pass@k", no_trace: null },
    { name: "regrade", kind: "trials", title: "Regrade", no_trace: NO_TRACE },
  ],
  trials: { diff: "verifier/patch.diff", tests: null },
};
const files: Record<string, string> = {
  "artifacts/solve/1/trial-1/trace.jsonl": solved,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input, "http://viz");
      if (url.pathname.endsWith("/record"))
        return Promise.resolve(Response.json(regrade));
      const path = url.searchParams.get("path") ?? "";
      const file = files[path];
      return Promise.resolve(
        file === undefined
          ? Response.json(
              { error: "not found", reason: `${path} does not exist` },
              { status: 404 },
            )
          : new Response(file),
      );
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Where the page is, as the address bar would show it. */
function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}

function show(at: string) {
  render(
    <ProjectsContext value={{ state: "ok", data: [project] }}>
      <MemoryRouter initialEntries={[`/regrade/attempt?id=regrade${at}`]}>
        <Routes>
          <Route
            path=":project/attempt"
            element={
              <>
                <AttemptPage />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const where = () =>
  new URLSearchParams(screen.getByTestId("where").textContent);
const trial = (name: string) =>
  screen.getByRole("button", { name: new RegExp(`^${name}`) });
const numbers = () =>
  within(screen.getByText("Reward").closest("dl") ?? document.body);
const field = (label: string) =>
  numbers().getByText(label, { selector: "dt" }).nextElementSibling;

describe("a trial that judged another trial's output again", () => {
  it("opens as the address names it, with the trial it judged in its numbers", async () => {
    show("&tab=trials&deliverable=regrade&trial=regrade-2");

    expect(
      await screen.findByRole("button", { name: "Regrade" }),
    ).toHaveAttribute("aria-haspopup", "menu");
    expect(trial("Trial 2")).toHaveAttribute("aria-pressed", "true");
    expect(field("ID")).toHaveTextContent("2__rg2x");
    expect(field("Source trial")).toHaveTextContent("Pass@k · Trial 2");
  });

  it("says why it has no trace, and links to the trace of the trial it judged", async () => {
    show("&tab=trials&deliverable=regrade&trial=regrade-1");

    const trace = await screen.findByRole("tabpanel", { name: "Trace" });
    expect(
      within(trace).getByText("This trial recorded no trace of its own."),
    ).toBeVisible();
    expect(within(trace).getByText(NO_TRACE)).toBeVisible();
    const link = within(trace).getByRole("link", { name: "Pass@k · Trial 1" });
    expect(trace).toHaveTextContent(
      "It judged again the output of Pass@k · Trial 1, whose trace is there.",
    );

    fireEvent.click(link);
    expect(where().get("tab")).toBe("trials");
    expect(where().get("deliverable")).toBe("pass_at_k");
    expect(where().get("trial")).toBe("trial-1");
    expect(screen.getByRole("button", { name: "Pass@k" })).toBeVisible();
    expect(trial("Trial 1")).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("Expand all")).toBeVisible();
  });

  it("names the path of a trial it judged that no deliverable lists", async () => {
    show("&tab=trials&deliverable=regrade&trial=regrade-3");

    const trace = await screen.findByRole("tabpanel", { name: "Trace" });
    expect(trace).toHaveTextContent(
      "It judged again the output of artifacts/solve/0/trial-9 (listed by no deliverable).",
    );
    expect(within(trace).queryByRole("link")).toBeNull();
    expect(field("Source trial")).toHaveTextContent(
      "artifacts/solve/0/trial-9 (listed by no deliverable)",
    );
  });

  it("shows the project's words alone when the trial judged none", async () => {
    show("&tab=trials&deliverable=regrade&trial=regrade-4");

    const trace = await screen.findByRole("tabpanel", { name: "Trace" });
    expect(
      within(trace).getByText("This trial recorded no trace."),
    ).toBeVisible();
    expect(within(trace).getByText(NO_TRACE)).toBeVisible();
    expect(trace).not.toHaveTextContent("It judged again");
    expect(numbers().queryByText("Source trial")).toBeNull();
  });

  it("shows no exception as a dash, and says when no cost was reported", async () => {
    show("&tab=trials&deliverable=regrade&trial=regrade-1");

    await screen.findByRole("tabpanel", { name: "Trace" });
    expect(field("Exception")).toHaveTextContent(/^—$/);
    expect(field("Cost")).toHaveTextContent("$0.00No cost reported.");
  });
});

describe("the address of a trials tab", () => {
  it("takes the deliverable and the trial chosen", async () => {
    show("&tab=trials");

    const menu = await screen.findByRole("button", { name: "Pass@k" });
    fireEvent.click(trial("Trial 3"));
    expect(where().get("deliverable")).toBe("pass_at_k");
    expect(where().get("trial")).toBe("trial-3");

    fireEvent.pointerDown(menu, { button: 0, ctrlKey: false });
    fireEvent.click(screen.getByRole("menuitem", { name: "Regrade" }));
    expect(where().get("deliverable")).toBe("regrade");
    // A trial of the deliverable left is not one of this one.
    expect(where().get("trial")).toBeNull();
    expect(trial("Trial 1")).toHaveAttribute("aria-pressed", "true");
    expect(field("ID")).toHaveTextContent("1__rg1x");
  });

  it("opens the first deliverable and trial for names it does not know", async () => {
    show("&tab=trials&deliverable=nothing&trial=nothing");

    expect(await screen.findByRole("button", { name: "Pass@k" })).toBeVisible();
    expect(trial("Trial 1")).toHaveAttribute("aria-pressed", "true");
  });
});
