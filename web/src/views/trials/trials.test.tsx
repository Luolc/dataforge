import {
  cleanup,
  fireEvent,
  render as plain,
  screen,
  within,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import midway from "../../../fixtures/live-deliverables/attempts/task-0301/2026-03-04T101500Z/record.json";
import passAtK from "../../../fixtures/p3/attempts/pass-at-k/record.json";
import solved from "../../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-1/trace.jsonl?raw";
import unsolved from "../../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-2/trace.jsonl?raw";
import diff from "../../../fixtures/trialdiff/attempts/pass-at-k/artifacts/solve/1/trial-1/verifier/patch.diff?raw";
import { must, sampleProject } from "../../../fixtures/sample";
import { type LiveInfo, type Project, record } from "../../api/types";
import TrialsView from ".";

// The views read the page's query.
const render = (ui: ReactElement) => plain(ui, { wrapper: MemoryRouter });

const project: Project = {
  ...sampleProject,
  statuses: { in_range: { label: "In range", tone: "good" } },
};
const attempt = record.parse(passAtK);
const traces: Record<string, string> = {
  "artifacts/solve/1/trial-1/trace.jsonl": solved,
  "artifacts/solve/1/trial-2/trace.jsonl": unsolved,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const path = new URL(url, "http://x").searchParams.get("path") ?? "";
      return Promise.resolve(new Response(traces[path] ?? ""));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function show() {
  render(
    <TrialsView
      project={project}
      attempt="pass-at-k"
      record={attempt}
      deliverable={must(attempt.deliverables?.[0])}
    />,
  );
}

/** The value of a field of the chosen trial's numbers, by its label. */
const field = (label: string) =>
  must(
    within(must(screen.getByText("Reward").closest("dl"))).getByText(label, {
      selector: "dt",
    }).nextElementSibling,
  );

const trial = (id: string) =>
  screen.getByRole("button", { name: new RegExp(`^${id}`) });

describe("the trials view", () => {
  it("lists the trials under the value and the visit's outcome", () => {
    show();

    const list = screen.getByRole("navigation", { name: "Trials" });
    expect(within(list).getByText("3 / 5 solved")).toBeVisible();
    expect(within(list).getByText("In range")).toBeVisible();
    expect(
      within(list)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "Trial 1solved",
      "Trial 2not solved",
      "Trial 3exception",
      "Trial 4solved",
      "Trial 5solved",
    ]);
  });

  it("puts the list above the trial when the two do not fit side by side", () => {
    show();
    const grid = screen.getByRole("navigation", {
      name: "Trials",
    }).parentElement;

    expect(grid?.parentElement).toHaveClass("@container");
    expect(grid?.className).toMatch(/(^| )@3xl:grid-cols-\[/);
    expect(grid?.className).not.toMatch(/(^| )grid-cols-/);
  });

  it("heads a visit with no outcome as No outcome once its attempt ended", () => {
    const open = structuredClone(attempt);
    open.status = null;
    for (const visit of open.visits) visit.outcome = null;
    const view = (shown: Project) =>
      render(
        <TrialsView
          project={shown}
          attempt="pass-at-k"
          record={open}
          deliverable={must(open.deliverables?.[0])}
        />,
      );
    const heading = () =>
      within(screen.getByRole("navigation", { name: "Trials" })).getByText(
        /^Outcome/,
      );

    view({ ...project, not_ended: ["running"] });
    expect(heading()).toHaveTextContent("Outcome No outcome");
    cleanup();

    view(project);
    expect(heading()).toHaveTextContent("Outcome Not ended");
  });

  it("shows the chosen trial's numbers and its session", async () => {
    show();

    expect(trial("Trial 1")).toHaveAttribute("aria-pressed", "true");
    expect(field("ID")).toHaveTextContent("tinyparse-fold__p3a1");
    expect(screen.getByText("9m 3s")).toBeVisible();
    expect(field("Cost")).toHaveTextContent(/^\$0\.31$/);
    expect(await screen.findByText("Expand all")).toBeVisible();
  });

  it("takes each trial's duration and cost from its own action", () => {
    show();
    fireEvent.click(trial("Trial 5"));

    const numbers = must(screen.getByText("Reward").closest("dl"));
    expect(within(numbers).getByText("24m 5s")).toBeVisible();
    expect(within(numbers).getByText("$0.62")).toBeVisible();
  });

  it("shows no duration or cost when no trial action holds the output", () => {
    const visit = must(attempt.visits[0]);
    const actions = visit.actions.map((action) =>
      action.kind === "trial" && "trial-1" in action.outputs
        ? { ...action, kind: "script" }
        : action,
    );
    render(
      <TrialsView
        project={project}
        attempt="pass-at-k"
        record={{ ...attempt, visits: [{ ...visit, actions }] }}
        deliverable={must(attempt.deliverables?.[0])}
      />,
    );

    expect(field("Duration")).toHaveTextContent(/^—$/);
    expect(field("Cost")).toHaveTextContent(/^—$/);
  });

  it("takes the badge from the reward, not from the agent's last word", async () => {
    show();
    fireEvent.click(trial("Trial 2"));

    expect(
      await screen.findByText("All tests pass, the task is done."),
    ).toBeVisible();
    const header = must(screen.getByText("Expand all").closest("header"));
    expect(within(header).getByText(/not solved · reward 0/)).toBeVisible();
  });

  it("says when a trial recorded no trace, and shows its exception", () => {
    show();
    fireEvent.click(trial("Trial 3"));

    expect(screen.getByText("This trial recorded no trace.")).toBeVisible();
    expect(
      screen.getByText("AgentTimeoutError: the agent ran past its 1800s limit"),
    ).toBeVisible();
  });

  it("reads a record written before a trial had a source", () => {
    // The record format's version 1, as every attempt before it has.
    expect(JSON.stringify(passAtK)).not.toContain('"source"');
    show();
    fireEvent.click(trial("Trial 3"));

    expect(screen.getByText("This trial recorded no trace.")).toBeVisible();
    expect(screen.queryByText("Source trial")).toBeNull();
    expect(screen.queryByText(/judged again/)).toBeNull();
  });
});

describe("the trials view's Diff tab", () => {
  const withDiff: Project = {
    ...project,
    trials: { diff: "verifier/patch.diff" },
  };
  const at = (n: number) =>
    `artifacts/solve/1/trial-${n.toString()}/verifier/patch.diff`;

  // The files route as the backend answers it: a file's bytes, a directory's
  // listing as JSON, a 413 over the limit and a 404 for anything else.
  function serve(
    files: Record<
      string,
      string | Uint8Array<ArrayBuffer> | { status: number }
    >,
  ) {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const path = new URL(url, "http://x").searchParams.get("path") ?? "";
        const found = files[path] ?? traces[path];
        if (typeof found === "string" || found instanceof Uint8Array)
          return Promise.resolve(new Response(found));
        if (found?.status === 200)
          return Promise.resolve(Response.json({ entries: [] }));
        const status = found?.status ?? 404;
        const reason =
          status === 413
            ? "17.0 MiB, over FILE_LIMIT (16 MiB)"
            : `${path}: No such file or directory`;
        return Promise.resolve(
          Response.json({ error: "x", reason }, { status }),
        );
      }),
    );
  }

  function showDiff(shown: Project = withDiff) {
    render(
      <TrialsView
        project={shown}
        attempt="pass-at-k"
        record={attempt}
        deliverable={must(attempt.deliverables?.[0])}
      />,
    );
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Diff" }));
  }

  it("opens on the Trace tab, Diff after it", () => {
    serve({});
    show();

    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Trace",
      "Diff",
    ]);
    expect(screen.getByRole("tab", { name: "Trace" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows the file the project names under the trial, coloured by line", async () => {
    serve({ [at(1)]: diff });
    showDiff();

    expect(await screen.findByText(at(1))).toBeVisible();
    const kinds = new Set(
      [...document.querySelectorAll("[data-line]")].map((line) =>
        line.getAttribute("data-line"),
      ),
    );
    expect(kinds).toEqual(
      new Set(["head", "hunk", "context", "add", "remove"]),
    );
  });

  it("says when the project names no diff", () => {
    serve({ [at(1)]: diff });
    showDiff(project);

    expect(
      screen.getByText("The project names no diff for its trials."),
    ).toBeVisible();
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("patch.diff"),
      expect.anything(),
    );
  });

  it("says when a trial recorded no diff, with the route's reason", async () => {
    serve({});
    showDiff();

    expect(
      await screen.findByText(/This trial recorded no diff\./),
    ).toBeVisible();
    expect(
      screen.getByText(`${at(1)}: No such file or directory`),
    ).toBeVisible();
  });

  it("tells a directory at the diff's path from a diff", async () => {
    serve({ [at(1)]: { status: 200 } });
    showDiff();

    expect(
      await screen.findByText(/This trial recorded no diff\./),
    ).toBeVisible();
    expect(screen.getByText(`${at(1)}: a directory, not a file`)).toBeVisible();
  });

  it("shows the reason of a diff over the limit", async () => {
    serve({ [at(1)]: { status: 413 } });
    showDiff();

    expect(
      await screen.findByText("Not shown: 17.0 MiB, over FILE_LIMIT (16 MiB)"),
    ).toBeVisible();
  });

  it("keeps the Diff tab when another trial is chosen", async () => {
    serve({ [at(1)]: diff, [at(2)]: diff });
    showDiff();
    fireEvent.click(trial("Trial 2"));

    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByText(at(2))).toBeVisible();
  });

  it("says when the diff is empty", async () => {
    serve({ [at(1)]: "" });
    showDiff();

    expect(
      await screen.findByText("The diff is empty: the trial changed nothing."),
    ).toBeVisible();
  });

  it("shows only the size of a diff that is not UTF-8", async () => {
    serve({ [at(1)]: new Uint8Array([0xff, 0xfe]) });
    showDiff();

    expect(
      await screen.findByText("Not shown: not UTF-8 text, 2 B."),
    ).toBeVisible();
  });

  it("keeps the trace loaded when going back from Diff", async () => {
    serve({ [at(1)]: diff });
    showDiff();
    await screen.findByText(at(1));
    expect(await screen.findByText("Expand all")).not.toBeVisible();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Trace" }));

    expect(await screen.findByText("Expand all")).toBeVisible();
    const reads = vi
      .mocked(fetch)
      .mock.calls.filter(
        ([url]) => typeof url === "string" && url.includes("trace.jsonl"),
      );
    expect(reads).toHaveLength(1);
  });
});

describe("the trials of a visit that has not ended", () => {
  const open = record.parse(midway);
  const pass = must(open.deliverables?.find((d) => d.kind === "trials"));
  const info = (live: LiveInfo["live"]): LiveInfo => ({
    live,
    held: live === "running",
    current: null,
  });
  const view = (live: LiveInfo | null) =>
    render(
      <TrialsView
        project={project}
        attempt="task-0301/2026-03-04T101500Z"
        record={open}
        deliverable={pass}
        live={live}
      />,
    );
  const rows = () =>
    within(screen.getByRole("navigation", { name: "Trials" }))
      .getAllByRole("button")
      .map((b) => b.textContent);

  it("lists each trial that has started, turning while it runs", () => {
    view(info("running"));

    expect(rows()).toEqual([
      "Trial 1solved",
      "Trial 2not solved",
      "Trial 3running",
      "Trial 4running",
    ]);
    // The word says it, so the turning icon is not read out as well.
    const spinner = must(
      trial("Trial 3").querySelector(".lucide-loader-circle"),
    );
    expect(spinner).toHaveClass("animate-spin", "motion-reduce:animate-none");
    expect(spinner.closest("[aria-hidden]")).not.toBeNull();
    expect(trial("Trial 3")).toHaveAccessibleName("Trial 3running");
    expect(screen.getByText("— / 4 solved")).toBeVisible();
    // The value is read once the visit ends, so its reason is not shown.
    expect(screen.queryByText(/has no key/)).toBeNull();
  });

  it("shows a running trial's page as running", () => {
    view(info("running"));
    fireEvent.click(trial("Trial 4"));

    expect(trial("Trial 4")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("This trial is still running.")).toBeVisible();
    for (const label of [
      "ID",
      "Reward",
      "Exception",
      "Duration",
      "Cost",
      "Trace format",
    ])
      expect(field(label)).toHaveTextContent(/^—$/);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Diff" }));
    expect(screen.getAllByText("This trial is still running.")).toHaveLength(2);
  });

  it.each([
    ["may have stopped", info("may have stopped")],
    ["not known", null],
  ])("stands still once the attempt %s", (_, live) => {
    view(live);

    expect(rows()).toEqual([
      "Trial 1solved",
      "Trial 2not solved",
      "Trial 3not finished",
      "Trial 4not finished",
    ]);
    expect(document.querySelector(".lucide-loader-circle")).toBeNull();
    fireEvent.click(trial("Trial 3"));
    expect(screen.getByText("This trial did not finish.")).toBeVisible();
  });
});
