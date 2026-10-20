import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import passAtK from "../../../fixtures/p3/attempts/pass-at-k/record.json";
import solved from "../../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-1/trace.jsonl?raw";
import unsolved from "../../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-2/trace.jsonl?raw";
import { must, sampleProject } from "../../../fixtures/sample";
import { type Project, record } from "../../api/types";
import TrialsView from ".";

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
      deliverable={must(attempt.deliverables[0])}
    />,
  );
}

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
      "tinyparse-fold__p3a1solved",
      "tinyparse-fold__p3b2not solved",
      "tinyparse-fold__p3c3exception",
      "tinyparse-fold__p3d4solved",
      "tinyparse-fold__p3e5solved",
    ]);
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
          deliverable={must(open.deliverables[0])}
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

    expect(trial("tinyparse-fold__p3a1")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("9m 3s")).toBeVisible();
    expect(screen.getByText("$0.31")).toBeVisible();
    expect(await screen.findByText("Hide thinking")).toBeVisible();
  });

  it("takes each trial's duration and cost from its own action", () => {
    show();
    fireEvent.click(trial("tinyparse-fold__p3e5"));

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
        deliverable={must(attempt.deliverables[0])}
      />,
    );

    const numbers = must(screen.getByText("Reward").closest("dl"));
    expect(within(numbers).getAllByText("—")).toHaveLength(2);
  });

  it("takes the badge from the reward, not from the agent's last word", async () => {
    show();
    fireEvent.click(trial("tinyparse-fold__p3b2"));

    expect(
      await screen.findByText("All tests pass, the task is done."),
    ).toBeVisible();
    const header = must(screen.getByText("Hide thinking").parentElement);
    expect(within(header).getByText(/not solved · reward 0/)).toBeVisible();
  });

  it("says when a trial recorded no trace, and shows its exception", () => {
    show();
    fireEvent.click(trial("tinyparse-fold__p3c3"));

    expect(screen.getByText("This trial recorded no trace.")).toBeVisible();
    expect(
      screen.getByText("AgentTimeoutError: the agent ran past its 1800s limit"),
    ).toBeVisible();
  });
});
