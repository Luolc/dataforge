import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import regrade from "../../../fixtures/regrade/attempts/regrade/record.json";
import logged from "../../../fixtures/regrade/attempts/regrade/artifacts/regrade/1/regrade-1/harbor.log?raw";
import { must, sampleProject } from "../../../fixtures/sample";
import type { Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { AttemptPage } from "../attempt/AttemptPage";

// As web/fixtures/regrade/project.yaml gives it.
const project: Project = {
  ...sampleProject,
  id: "regrade",
  states: { solve: { title: "Pass@k" }, regrade: { title: "Regrade" } },
  statuses: {
    ...sampleProject.statuses,
    held: { label: "Held", tone: "good" },
    broke: { label: "Broke", tone: "bad" },
  },
  deliverables: [
    { name: "pass_at_k", kind: "trials", title: "Pass@k" },
    { name: "regrade", kind: "trials", title: "Regrade", count: "held" },
  ],
  trials: { diff: "verifier/patch.diff", tests: null },
};
const files: Record<string, string> = {
  "artifacts/regrade/1/regrade-1/harbor.log": logged,
  "artifacts/regrade/1/regrade-3/harbor.log": "",
};

function show(at: string, record: unknown = regrade) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input, "http://viz");
      if (url.pathname.endsWith("/record"))
        return Promise.resolve(Response.json(record));
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
  render(
    <ProjectsContext value={{ state: "ok", data: [project] }}>
      <MemoryRouter initialEntries={[`/regrade/attempt?id=regrade${at}`]}>
        <Routes>
          <Route path=":project/attempt" element={<AttemptPage />} />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The record with its regrade deliverable changed as `change` says. */
function withRegrade(change: Record<string, unknown>): unknown {
  return {
    ...regrade,
    deliverables: regrade.deliverables.map((d) =>
      d.name === "regrade" ? { ...d, ...change } : d,
    ),
  };
}

const trial = (name: string) =>
  screen.getByRole("button", { name: new RegExp(`^${name}`) });
const numbers = () => within(must(screen.getByText("Reward").closest("dl")));
const field = (label: string) =>
  must(
    numbers()
      .getByText(label, { selector: "dt" })
      .parentElement?.querySelector<HTMLElement>("dd"),
  );

describe("a trial's verdict", () => {
  it("is the project's word for it, with its tone, else solved or not", async () => {
    show("&tab=trials&deliverable=regrade");

    await screen.findByRole("button", { name: "Regrade" });
    expect(trial("Trial 1")).toHaveTextContent(/Held$/);
    expect(within(trial("Trial 1")).getByText("Held")).toHaveClass("text-good");
    expect(trial("Trial 2")).toHaveTextContent(/Broke$/);
    expect(within(trial("Trial 2")).getByText("Broke")).toHaveClass("text-bad");
    // One with no verdict falls back to its reward.
    expect(trial("Trial 4")).toHaveTextContent(/solved$/);
  });

  it("says beside it that the trial also ended on an exception", async () => {
    show("&tab=trials&deliverable=regrade");

    await screen.findByRole("button", { name: "Regrade" });
    expect(trial("Trial 3")).toHaveTextContent(/Broke·exception$/);
    expect(within(trial("Trial 3")).getByText(/exception/)).toHaveClass(
      "text-warn",
    );
    expect(trial("Trial 2")).not.toHaveTextContent(/exception/);
  });

  it("is counted under the project's word in the list", async () => {
    show("&tab=trials&deliverable=regrade");

    expect(await screen.findByText("2 / 4 held")).toBeVisible();
  });

  it("leaves a deliverable whose project names no word counted solved", async () => {
    show("&tab=trials&deliverable=pass_at_k");

    expect(await screen.findByText("1 / 3 solved")).toBeVisible();
    expect(trial("Trial 1")).toHaveTextContent(/solved$/);
  });
});

describe("the Summary's line for a trials deliverable", () => {
  it("counts under the project's word", async () => {
    show("");

    const card = await screen.findByRole("table");
    expect(within(card).getByText("2 / 4 held")).toBeVisible();
    expect(within(card).getByText("1 / 3 solved")).toBeVisible();
  });
});

describe("the menu of a tab's trials deliverables", () => {
  it("gives each its count", async () => {
    show("&tab=trials");

    const menu = await screen.findByRole("button", { name: "Pass@k" });
    fireEvent.pointerDown(menu, { button: 0, ctrlKey: false });
    expect(screen.getByRole("menuitem", { name: /^Pass@k/ })).toHaveTextContent(
      "Pass@k1 / 3",
    );
    expect(
      screen.getByRole("menuitem", { name: /^Regrade/ }),
    ).toHaveTextContent("Regrade2 / 4");
  });
});

describe("a trial's exception", () => {
  it("shows its first line, and the rest under Show all", async () => {
    show("&tab=trials&deliverable=regrade&trial=regrade-3");

    await screen.findByRole("button", { name: "Regrade" });
    const exception = field("Exception");
    expect(exception).toHaveTextContent(
      /^VerifierTimeoutError: the tests ran past their 600s limitShow all$/,
    );
    const more = within(exception).getByRole("button", { name: "Show all" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    const shown = must(
      document.getElementById(must(more.getAttribute("aria-controls"))),
    );
    expect(exception).toContainElement(shown);

    fireEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(more).toHaveTextContent("Show less");
    expect(shown).toHaveTextContent(
      "the last test was tests/test_fold.py::test_nested",
    );
  });

  it("of one line shows it whole, with nothing to unfold", async () => {
    show("&tab=trials&deliverable=pass_at_k&trial=trial-3");

    await screen.findByRole("button", { name: "Pass@k" });
    const exception = field("Exception");
    expect(exception).toHaveTextContent(
      /^AgentTimeoutError: the agent ran past its 1800s limit$/,
    );
    expect(within(exception).queryByRole("button")).toBeNull();
  });
});

describe("a trial's numbers", () => {
  it("say what the trial time measures, and give no trace format", async () => {
    show("&tab=trials&deliverable=pass_at_k");

    await screen.findByRole("button", { name: "Pass@k" });
    expect(
      numbers().getByText("Trial time", { selector: "dt" }),
    ).toHaveAttribute(
      "title",
      "The whole trial: its environment, its agent and its verifier.",
    );
    expect(numbers().queryByText("Duration")).toBeNull();
    expect(numbers().queryByText("Trace format")).toBeNull();
  });
});

describe("a trial's log", () => {
  const log = async (output: string) => {
    show(`&tab=trials&deliverable=regrade&trial=${output}`);
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Log" }));
    return screen.getByRole("tabpanel", { name: "Log" });
  };

  it("shows the file the trial names, under its path", async () => {
    const panel = await log("regrade-1");

    expect(
      await within(panel).findByText(
        "artifacts/regrade/1/regrade-1/harbor.log",
      ),
    ).toBeVisible();
    expect(panel).toHaveTextContent("Trial 1__rg1x finished: reward 1.0");
  });

  it.each([
    ["regrade-2", "This trial's log is not there."],
    ["regrade-3", "The log is empty."],
    ["regrade-4", "This trial recorded no log."],
  ])("says why %s's is not shown", async (output, why) => {
    const panel = await log(output);

    expect(await within(panel).findByText(why)).toBeVisible();
  });

  it("has no tab where no trial of the deliverable names one", async () => {
    show("&tab=trials&deliverable=pass_at_k");

    await screen.findByRole("tab", { name: "Trace" });
    expect(screen.queryByRole("tab", { name: "Log" })).toBeNull();
  });
});

describe("a trials deliverable's heading", () => {
  it("links to the run that made it", async () => {
    show("&tab=trials&deliverable=regrade");

    const from = await screen.findByRole("link", { name: "Regrade · run 2" });
    expect(from).toHaveAttribute(
      "href",
      "/regrade/attempt?id=regrade&tab=runs&run=1",
    );
    expect(from.closest("p")).toHaveTextContent(/^From Regrade · run 2$/);
  });

  it("says that no count was recorded, and why under Why", async () => {
    show(
      "&tab=trials&deliverable=regrade",
      withRegrade({
        value: {
          output: null,
          field: ["solved"],
          value: null,
          reason: "the outcome data has no solved",
        },
      }),
    );

    expect(await screen.findByText("— / 4 held")).toBeVisible();
    const why = screen.getByRole("button", { name: "Why" });
    const reason = must(
      document.getElementById(must(why.getAttribute("aria-controls"))),
    );
    expect(why).toHaveAttribute("aria-expanded", "false");
    expect(reason).not.toBeVisible();

    fireEvent.click(why);
    expect(why).toHaveAttribute("aria-expanded", "true");
    expect(reason).toBeVisible();
    expect(reason).toHaveTextContent("the outcome data has no solved");
  });
});

describe("a trials deliverable the attempt did not reach", () => {
  it("is not run, its state named by its title", async () => {
    show(
      "&tab=trials&deliverable=regrade",
      withRegrade({
        path: null,
        visit: null,
        visit_index: null,
        value: null,
        verdicts: null,
        trials: [],
        reason: "regrade was never visited",
      }),
    );

    expect(
      await screen.findByText("Not run: Regrade was never visited"),
    ).toBeVisible();
  });
});
