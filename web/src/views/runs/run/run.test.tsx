import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import failedJson from "../../../../fixtures/attempts/p4/task-0101/record.json";
import manyJson from "../../../../fixtures/attempts/p4/task-0102/record.json";
import { must, records, sampleProject } from "../../../../fixtures/sample";
import { type AttemptRecord, record } from "../../../api/types";
import RunPage from ".";

const failed = record.parse(failedJson);
const many = record.parse(manyJson);
const project = {
  ...sampleProject,
  statuses: {
    ...sampleProject.statuses,
    error: { label: "Error", tone: "bad" as const },
  },
};

function show(rec: AttemptRecord, index: number, shown = project) {
  return render(
    <MemoryRouter>
      <RunPage
        project={shown}
        attempt="p4/task-0101"
        record={rec}
        visit={must(rec.visits[index])}
        index={index}
      />
    </MemoryRouter>,
  );
}

/** Each step's name and whether it is open, in order. */
function steps(): [string, boolean][] {
  return screen
    .getAllByRole("button")
    .map((b) => [b.textContent, b.getAttribute("aria-expanded") === "true"]);
}

/** The step at a place in the page's order, Input first. */
function stepAt(place: number): HTMLElement {
  return must(screen.getAllByRole("button")[place]?.closest("section"));
}

function step(name: RegExp): HTMLElement {
  return must(
    screen
      .getAllByRole("button")
      .find((b) => name.test(b.textContent))
      ?.closest("section"),
  );
}

describe("which steps start open", () => {
  it("a failed action and a bad outcome, with nothing else", () => {
    show(failed, 2);

    expect(steps().map(([, open]) => open)).toEqual([false, true, true]);
    expect(steps()[1]?.[0]).toMatch(/^agent/);
    expect(steps()[2]?.[0]).toMatch(/^Output/);
  });

  it("only the visit's first session when nothing failed", () => {
    show(records.passed, 5);

    expect(steps().map(([, open]) => open)).toEqual([
      false, // Input
      true, // trial-1, the first session
      false,
      false,
      false,
      false, // the closing script
      false, // Output, in range
    ]);
  });

  it("every failed action, but one session only", () => {
    show(many, 3);

    // Input, trial-1 to trial-12, the closing script, then Output.
    const open = steps().flatMap(([, isOpen], i) => (isOpen ? [i] : []));
    expect(open).toEqual([1, 7]);
  });

  it("a folded step draws no body until it is opened", () => {
    show(records.passed, 5);
    const trial2 = stepAt(2);
    expect(within(trial2).queryByText(/trial-2\/trace\.jsonl/)).toBeNull();

    fireEvent.click(within(trial2).getByRole("button"));

    expect(within(trial2).getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(trial2).getByText(/trial-2\/trace\.jsonl/)).toBeVisible();
  });

  it("another run's steps start as that run's rules say", () => {
    const page = (index: number) => (
      <MemoryRouter>
        <RunPage
          project={project}
          attempt="batch-1/task-0001"
          record={records.passed}
          visit={must(records.passed.visits[index])}
          index={index}
        />
      </MemoryRouter>
    );
    const { rerender } = render(page(5));
    fireEvent.click(within(stepAt(5)).getByRole("button"));

    rerender(page(1));

    // Input, the script, then the Output of a failed check.
    expect(steps().map(([, open]) => open)).toEqual([false, false, true]);
  });

  it("each toggle names the body it opens", () => {
    show(records.passed, 0);

    for (const button of screen.getAllByRole("button")) {
      const body = button.getAttribute("aria-controls");
      expect(body).not.toBeNull();
      expect(document.getElementById(must(body))).not.toBeNull();
    }
  });
});

describe("an action's fields", () => {
  it("shows its kind, status, cost, times and outputs", () => {
    show(records.passed, 4);
    const judge = step(/^judge/);
    fireEvent.click(within(judge).getByRole("button"));

    expect(within(judge).getByRole("button")).toHaveTextContent(
      "judge · report, scores2m 3s · $0.42",
    );
    expect(within(judge).getByText("OK")).toBeVisible();
    expect(within(judge).getByText("2026-03-02 09:43 UTC")).toBeVisible();
    expect(within(judge).getByText("2026-03-02 09:45 UTC")).toBeVisible();
    expect(
      within(judge).getByText("artifacts/review/1/scores.json"),
    ).toBeVisible();
    expect(within(judge).getByText(/4 of 4 criteria/)).toBeVisible();
  });

  it("a trial's session shows its trace from the deliverable", () => {
    show(records.passed, 5);

    expect(
      within(stepAt(1)).getByText("artifacts/solve/1/trial-1/trace.jsonl"),
    ).toBeVisible();
  });

  it("an agent's session shows the trace its action names", () => {
    show(records.resumed, 0);

    expect(within(stepAt(1)).getByText("runs/author-1.jsonl")).toBeVisible();
  });
});

describe("the input and the output", () => {
  it("names the report it was handed, with the run that handed it", () => {
    show(failed, 2);
    const input = step(/^Input/);
    fireEvent.click(within(input).getByRole("button"));

    expect(within(input).getByRole("link")).toHaveAttribute(
      "href",
      "/sample/attempt?id=p4%2Ftask-0101&tab=runs&run=1",
    );
    expect(within(input).getByRole("link")).toHaveTextContent("check · run 2");
    expect(within(input).getByText("Fail")).toBeVisible();
    expect(
      within(input).getByText("artifacts/check/1/feedback.md"),
    ).toBeVisible();
  });

  it("a first visit, or one whose transition hands no report, has none", () => {
    show(records.passed, 0);
    fireEvent.click(within(step(/^Input/)).getByRole("button"));

    expect(screen.getByText("The visit was handed no report.")).toBeVisible();
  });

  it("a transition that hands no report leaves the visit none", () => {
    const rec = structuredClone(failed);
    for (const edge of must(rec.graph).edges) edge.report = false;
    show(rec, 2);
    fireEvent.click(within(step(/^Input/)).getByRole("button"));

    expect(screen.getByText("The visit was handed no report.")).toBeVisible();
  });

  it("shows the outcome, the next run and the mark", () => {
    const rec = structuredClone(records.passed);
    must(rec.visits[1]).mark = "too_easy";
    show(rec, 1);
    const output = step(/^Output/);

    expect(within(output).getAllByText("Fail")).not.toHaveLength(0);
    expect(within(output).getByRole("link")).toHaveTextContent(
      "author · run 3",
    );
    expect(within(output).getByText("too_easy")).toBeVisible();
  });

  it("the last run leads to the end and the attempt's status", () => {
    const rec = structuredClone(failed);
    show(rec, 2);

    expect(step(/^Output/)).toHaveTextContent("NextEnd · Error");
  });

  it("a status the project names as not ended leads to Not ended", () => {
    const rec = { ...structuredClone(failed), status: "running" };
    show(rec, 2, { ...project, not_ended: ["running"] });

    expect(step(/^Output/)).toHaveTextContent("NextNot ended");
  });

  it("a visit with no outcome is No outcome once its attempt has ended", () => {
    const ended = { ...project, not_ended: ["running"] };
    show(records.running, 1, ended);
    fireEvent.click(within(step(/^Output/)).getByRole("button"));

    expect(step(/^Output/)).toHaveTextContent("OutcomeNo outcome");
    expect(screen.queryByText("Not ended")).toBeNull();
  });

  it("a visit with no outcome is Not ended while its attempt runs", () => {
    show(records.running, 1);
    fireEvent.click(within(step(/^Output/)).getByRole("button"));

    expect(step(/^Output/)).toHaveTextContent("OutcomeNot ended");
  });
});

describe("what the record does not have", () => {
  it("a record with no graph cannot tell the input", () => {
    const rec = { ...structuredClone(failed), graph: null };
    show(rec, 2);
    fireEvent.click(within(step(/^Input/)).getByRole("button"));

    expect(screen.getByText(/This record has no graph/)).toBeVisible();
  });

  it("an agent with no trace, no outputs and times that do not parse", () => {
    const rec = structuredClone(failed);
    const agent = must(must(rec.visits[2]).actions[0]);
    agent.started_at = "not a time";
    show(rec, 2);
    const step3 = step(/^agent/);

    expect(within(step3).getByRole("button")).toHaveTextContent(
      "agent— · $2.75",
    );
    expect(within(step3).getAllByText("—")).not.toHaveLength(0);
    expect(within(step3).getByText("none")).toBeVisible();
    expect(
      within(step3).getByText("The record names no trace for this session."),
    ).toBeVisible();
  });

  it("a visit with no outcome and no actions has not ended", () => {
    const rec = structuredClone(records.running);
    must(rec.visits[1]).actions = [];
    show(rec, 1);
    fireEvent.click(within(step(/^Output/)).getByRole("button"));

    expect(screen.getByText("The visit recorded no actions.")).toBeVisible();
    expect(within(step(/^Output/)).getAllByText("Not ended")).toHaveLength(2);
    expect(within(step(/^Output/)).getAllByText("none")).toHaveLength(2);
  });
});
