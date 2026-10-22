import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import passAtK from "../../../fixtures/trialdiff/attempts/pass-at-k/record.json";
import many from "../../../fixtures/trialdiff/attempts/pass-at-k/artifacts/solve/1/trial-1/verifier/ctrf.json?raw";
import failing from "../../../fixtures/trialdiff/attempts/pass-at-k/artifacts/solve/1/trial-2/verifier/ctrf.json?raw";
import notCtrf from "../../../fixtures/trialdiff/attempts/pass-at-k/artifacts/solve/1/trial-4/verifier/ctrf.json?raw";
import other from "../../../fixtures/trialdiff/attempts/pass-at-k/artifacts/solve/1/trial-5/verifier/ctrf.json?raw";
import midway from "../../../fixtures/live-deliverables/attempts/task-0301/2026-03-04T101500Z/record.json";
import { must, sampleProject } from "../../../fixtures/sample";
import { type LiveInfo, type Project, record } from "../../api/types";
import TrialsView from ".";
import { readCtrf, suiteOf } from "./ctrf";

const project: Project = {
  ...sampleProject,
  trials: {
    diff: "verifier/patch.diff",
    tests: "verifier/ctrf.json",
    kinds: {
      security: { label: "Security" },
      behavior: { label: "Behavior" },
    },
  },
};
const attempt = record.parse(passAtK);
const at = (n: number) =>
  `artifacts/solve/1/trial-${n.toString()}/verifier/ctrf.json`;
const reports: Record<string, string> = {
  [at(1)]: many,
  [at(2)]: failing,
  [at(4)]: notCtrf,
  [at(5)]: other,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const path = new URL(url, "http://x").searchParams.get("path") ?? "";
      const found = reports[path];
      if (found !== undefined) return Promise.resolve(new Response(found));
      if (path.endsWith("ctrf.json"))
        return Promise.resolve(
          Response.json(
            { error: "x", reason: `${path}: No such file or directory` },
            { status: 404 },
          ),
        );
      return Promise.resolve(new Response(""));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function showTests(trialNumber = 1, shown: Project = project) {
  render(
    <TrialsView
      project={shown}
      attempt="pass-at-k"
      record={attempt}
      deliverable={must(attempt.deliverables?.[0])}
    />,
  );
  if (trialNumber !== 1)
    fireEvent.click(
      screen.getByRole("button", {
        name: new RegExp(`^tinyparse-fold__p3.${trialNumber.toString()}`),
      }),
    );
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Tests" }));
}

/** A cell's text, without its buttons'. */
function cellText(td: HTMLTableCellElement): string {
  return [...td.childNodes]
    .filter((node) => node.nodeName !== "BUTTON")
    .map((node) => node.textContent ?? "")
    .join("")
    .trim();
}

/** The table's rows of tests, each as its cells' text. */
function rows() {
  return [...document.querySelectorAll("tbody:not([hidden]) > tr")]
    .filter(
      (tr) => !tr.hasAttribute("hidden") && tr.querySelector("td") !== null,
    )
    .map((tr) => [...tr.querySelectorAll("td")].map(cellText));
}

const groupButton = (suite: string) =>
  screen.getByRole("button", { name: new RegExp(`^${suite}`) });

describe("the trials view's Tests tab", () => {
  it("comes after Trace and Diff, only for a project that names its tests", () => {
    showTests();

    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Trace",
      "Diff",
      "Tests",
    ]);
  });

  it("is not there when the project names no tests", () => {
    render(
      <TrialsView
        project={{ ...project, trials: { diff: "verifier/patch.diff" } }}
        attempt="pass-at-k"
        record={attempt}
        deliverable={must(attempt.deliverables?.[0])}
      />,
    );

    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Trace",
      "Diff",
    ]);
  });

  it("lists each test under its suite with its status, name, kind, principle, runner and duration", async () => {
    showTests();

    expect(await screen.findByText(at(1))).toBeVisible();
    expect(groupButton("f2p")).toHaveTextContent(/^f2p4$/);
    expect(groupButton("p2p")).toHaveTextContent(/^p2p40$/);
    expect(rows().slice(0, 4)).toEqual([
      [
        "passed",
        "tests/test_fold_deep.py::test_fold_rejects_a_nesting_past_the_limit",
        "Security",
        "p1",
        "pytest",
        "41ms",
      ],
      [
        "passed",
        "tests/test_fold_deep.py::test_fold_keeps_a_nesting_at_the_limit",
        "Behavior",
        "p1",
        "pytest",
        "37ms",
      ],
      [
        "passed",
        "tests/test_fold_deep.py::test_fold_reports_where_it_stopped",
        "Behavior",
        "—",
        "pytest",
        "1.4s",
      ],
      // A kind the project gives no label shows as itself.
      [
        "passed",
        "tests/test_fold_deep.py::test_fold_of_an_empty_document",
        "edge",
        "—",
        "pytest",
        "3ms",
      ],
    ]);
    const duration = screen.getByText("41ms");
    expect(duration).toHaveClass("text-right", "tabular-nums");
  });

  it("counts the failed tests of each suite, an error among them, in its row", async () => {
    showTests(2);

    expect(await screen.findByText(at(2))).toBeVisible();
    expect(groupButton("f2p")).toHaveTextContent(/^f2p2 of 3 failed$/);
    expect(groupButton("p2p")).toHaveTextContent(/^p2p1 of 3 failed$/);
    expect(rows().map((r) => r[0])).toEqual([
      "failed",
      "passed",
      "error",
      "passed",
      "error",
      "passed",
    ]);
  });

  it("folds a suite under its row", async () => {
    showTests();
    await screen.findByText(at(1));
    const button = groupButton("p2p");
    const group = must(
      document.getElementById(must(button.getAttribute("aria-controls"))),
    );

    expect(button).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(group).not.toBeVisible();
  });

  it("opens a test's message and trace from its row", async () => {
    showTests(2);
    await screen.findByText(at(2));
    const [first] = screen.getAllByRole("button", { name: "Message" });
    const message =
      "AssertionError: expected FoldError, got a tree 4096 levels deep";
    const detail = must(
      document.getElementById(must(must(first).getAttribute("aria-controls"))),
    );

    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(within(detail).getByText(message)).not.toBeVisible();
    fireEvent.click(must(first));
    expect(first).toHaveAttribute("aria-expanded", "true");
    expect(within(detail).getByText(message)).toBeVisible();
    expect(within(detail).getByText(/DID NOT RAISE/)).toBeVisible();
  });

  it("filters by status and says how many it hides", async () => {
    showTests(2);
    await screen.findByText(at(2));
    const chips = screen.getByRole("group", { name: "Filter by status" });

    expect(
      within(chips)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["All6", "passed3", "failed1", "error2"]);
    fireEvent.click(within(chips).getByRole("button", { name: /^error/ }));
    expect(rows().map((r) => r[0])).toEqual(["error", "error"]);
    expect(
      screen.getByText("4 of 6 hidden by the status filter."),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(rows()).toHaveLength(6);
    expect(screen.queryByText(/hidden by/)).toBeNull();
  });

  it("filters by kind, by the project's labels, and names both filters when both hide", async () => {
    showTests(2);
    await screen.findByText(at(2));
    const kinds = screen.getByRole("group", { name: "Filter by kind" });

    expect(
      within(kinds)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["All kinds6", "Security1", "Behavior2"]);
    fireEvent.click(within(kinds).getByRole("button", { name: /^Behavior/ }));
    expect(rows().map((r) => r[1])).toEqual([
      "tests/test_fold_deep.py::test_fold_keeps_a_nesting_at_the_limit",
      "tests/test_fold_deep.py::test_fold_reports_where_it_stopped",
    ]);
    expect(screen.getByText("4 of 6 hidden by the kind filter.")).toBeVisible();
    // A suite with nothing left to show has no row.
    expect(screen.queryByRole("button", { name: /^p2p/ })).toBeNull();
    fireEvent.click(
      within(screen.getByRole("group", { name: "Filter by status" })).getByRole(
        "button",
        { name: /^error/ },
      ),
    );
    expect(
      screen.getByText("5 of 6 hidden by the status and the kind filters."),
    ).toBeVisible();
  });

  it("leaves out the columns no test has a value for", async () => {
    showTests(5);
    await screen.findByText(at(5));

    expect(
      within(must(document.querySelector("thead")))
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["Status", "Name", "Runner", "Duration"]);
    expect(screen.queryByRole("group", { name: "Filter by kind" })).toBeNull();
  });

  it("says a trial with no results ran no tests, never that they failed or passed", async () => {
    showTests(3);

    expect(
      await screen.findByText(/This trial recorded no test results\./),
    ).toBeVisible();
    expect(
      screen.getByText(`${at(3)}: No such file or directory`),
    ).toBeVisible();
    expect(screen.queryByText(/^(failed|passed)$/)).toBeNull();
  });

  it("shows a file that is not a CTRF report as written, with why", async () => {
    showTests(4);

    expect(
      await screen.findByText(
        "This file is not a CTRF report (results.tests[0].status does not fit). Below is the file as written.",
      ),
    ).toBeVisible();
    expect(document.querySelector("pre code")).toHaveTextContent(
      '"status": "ok"',
    );
  });

  it("keeps the Tests tab when another trial is chosen", async () => {
    showTests();
    await screen.findByText(at(1));
    fireEvent.click(
      screen.getByRole("button", { name: /^tinyparse-fold__p3b2/ }),
    );

    expect(screen.getByRole("tab", { name: "Tests" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByText(at(2))).toBeVisible();
  });
});

describe("the Tests tab of a trial that has not finished", () => {
  const open = record.parse(midway);
  const pass = must(open.deliverables?.find((d) => d.kind === "trials"));
  const info: LiveInfo = { live: "running", held: true, current: null };

  it("says the trial is still running, and reads nothing", () => {
    render(
      <TrialsView
        project={project}
        attempt="task-0301/2026-03-04T101500Z"
        record={open}
        deliverable={pass}
        live={info}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^trial-3/ }));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Tests" }));

    expect(screen.getAllByText("This trial is still running.")).toHaveLength(2);
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("ctrf.json"),
      expect.anything(),
    );
  });
});

describe("reading a CTRF report", () => {
  it("takes a suite written as one string as a suite of one level", () => {
    const read = readCtrf(
      JSON.stringify({
        results: {
          tests: [{ name: "t", status: "passed", duration: 1, suite: "p2p" }],
        },
      }),
    );

    expect("tests" in read && read.tests.map(suiteOf)).toEqual(["p2p"]);
  });

  it("says why a text is not one", () => {
    expect(readCtrf("{")).toEqual({ why: "it is not JSON" });
    expect(readCtrf("[]")).toEqual({ why: "it is not an object" });
    expect(readCtrf("{}")).toEqual({ why: "results does not fit" });
  });
});
