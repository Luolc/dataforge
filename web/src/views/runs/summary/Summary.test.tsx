import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  inReview,
  mayHaveStopped,
  must,
  records,
  runningIn,
  sampleProject,
} from "../../../../fixtures/sample";
import type { AttemptRecord, LiveInfo } from "../../../api/types";
import { asShown } from "../../attempt/declared";
import { Summary } from "./Summary";

// The record as the attempt page hands it to the Summary.
function show(
  name: keyof typeof records | AttemptRecord,
  project = sampleProject,
) {
  const record = typeof name === "string" ? records[name] : name;
  return render(
    <MemoryRouter>
      <Summary
        project={project}
        attempt="batch-1/task-0002"
        record={asShown(project, record)}
      />
    </MemoryRouter>,
  );
}

describe("Summary", () => {
  it("keeps the deliverables and the reason, the heading having the rest", () => {
    show("passed");

    expect(screen.getByText("Deliverables", { selector: "dt" })).toBeVisible();
    for (const moved of ["Started", "Status", "Total duration", "Cost"])
      expect(screen.queryByText(moved, { selector: "dt" })).toBeNull();
  });

  it("draws the machine with its states linked to their last runs", () => {
    show("passed");

    const graph = screen.getByRole("group", { name: "State machine" });
    const solve = within(graph).getByRole("link", {
      name: /^Pass@k, .*: open its last run$/,
    });
    expect(solve).toHaveAttribute(
      "href",
      "/sample/attempt?id=batch-1%2Ftask-0002&tab=runs&run=5",
    );
    expect(graph.querySelectorAll('[data-taken="true"]')).toHaveLength(5);
    expect(graph.querySelectorAll('[data-taken="false"]')).toHaveLength(2);
  });

  it("renders the actions' summaries as untrusted Markdown", () => {
    const { container } = show("tooEasy");

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/raw HTML shows as text/)).toHaveTextContent(
      "<b>raw HTML shows as text</b>",
    );
    expect(screen.getByRole("link", { name: "image: a chart" })).toBeVisible();
  });

  it("links each deliverable to its tab, or says why it was not reached", () => {
    show("exhausted");

    expect(screen.getByRole("link", { name: "Pass@k" })).toHaveAttribute(
      "href",
      "/sample/attempt?id=batch-1%2Ftask-0002&tab=trials",
    );
    expect(
      screen.getByText("Not run: the attempt never reached Pass@k."),
    ).toBeVisible();
    expect(screen.queryByText(/was never visited/)).toBeNull();
  });

  it("counts the runs above the machine, one run as 1 run", () => {
    show("passed");
    expect(screen.getByText(/^6 runs · solid lines/)).toBeVisible();

    show({ ...records.passed, visits: records.passed.visits.slice(0, 1) });
    expect(screen.getByText(/^1 run · solid lines/)).toBeVisible();
  });

  it("scrolls the deliverables sideways in their card, its aside under the title when they do not fit", () => {
    show("exhausted");
    const card = screen.getByRole("heading", {
      name: "Deliverables",
    }).parentElement;

    expect(card).toHaveClass("flex-wrap");
    expect(
      within(card?.parentElement ?? document.body).getByRole("table")
        .parentElement,
    ).toHaveClass("overflow-x-auto");
  });
});

describe("the Summary's Task card", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("follows the machine for a project that names a task", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          Response.json({
            read: "not run",
            reason: "task: No such file or directory",
            path: "task/task.toml",
            legacy: false,
            tests: null,
            principles: null,
          }),
        ),
      ),
    );
    show("passed", {
      ...sampleProject,
      task: { file: "task/task.toml", deliverable: null },
    });

    const task = await screen.findByRole("heading", { name: "Task" });
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    expect(headings.indexOf("Task")).toBe(
      headings.indexOf("State machine") + 1,
    );
    expect(task.closest("section")).toHaveTextContent("Not run");
  });
});

describe("the Summary of a running attempt", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-02T09:45:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function showLive(live: LiveInfo) {
    render(
      <MemoryRouter>
        <Summary
          project={sampleProject}
          attempt="batch-1/task-0001"
          record={inReview}
          live={live}
        />
      </MemoryRouter>,
    );
    return {
      graph: screen.getByRole("group", { name: "State machine" }),
    };
  }

  it("draws the state it runs as running, the transition into it taken", () => {
    const { graph } = showLive(runningIn());
    const review = must(
      graph.querySelector<HTMLElement>('[data-node="review"]'),
    );
    const into = [...graph.querySelectorAll("[data-edge]")].find(
      (g) => g.getAttribute("data-edge") === "check\0review",
    );

    expect(
      within(review).getByRole("img", { name: "Running" }),
    ).toBeInTheDocument();
    // Here, with no canvas to measure on, the line may break before Running.
    expect(review).toHaveTextContent(/Run 1 ·\s*Running/);
    expect(review.querySelector("rect")).toHaveClass("stroke-accent");
    expect(into).toHaveAttribute("data-taken", "true");
    expect(review).toHaveTextContent("1m 21s");
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(review).toHaveTextContent("1m 25s");
  });

  it("draws no state as running when it may have stopped", () => {
    const { graph } = showLive(mayHaveStopped);

    expect(within(graph).queryByRole("img", { name: "Running" })).toBeNull();
  });
});
