import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  inReview,
  mayHaveStopped,
  must,
  records,
  runningIn,
  sampleProject,
} from "../../../fixtures/sample";
import type { Loaded } from "../../api/client";
import type { AttemptRecord, LiveInfo, Project } from "../../api/types";
import type { Snapshot } from "../definition/load";
import { RunsView } from "./RunsView";

function show(
  project: Project,
  record: AttemptRecord = records.running,
  discarded: number | null = null,
) {
  render(
    <MemoryRouter>
      <RunsView
        project={project}
        attempt="batch-2/task-0005"
        record={record}
        run={null}
        discarded={discarded}
      />
    </MemoryRouter>,
  );
  return within(screen.getByRole("navigation", { name: "Runs" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Runs column", () => {
  it("stands each run's icon on its title's line, not between the two lines", () => {
    const runs = show({ ...sampleProject, not_ended: ["running"] });

    // A box one line high at the top of the entry: centred on the title.
    const box = runs.getByRole("img", { name: "No outcome" }).parentElement;
    expect(box).toHaveClass("h-lh", "self-start", "items-center");
  });

  it("shows a run with no outcome as No outcome once its attempt ended", () => {
    const runs = show({ ...sampleProject, not_ended: ["running"] });

    expect(runs.getByRole("img", { name: "No outcome" })).toBeVisible();
    expect(runs.getByText(/^No outcome ·/)).toBeVisible();
    expect(runs.queryByText(/Not ended/)).toBeNull();
  });

  it("says a run with no action has no time recorded, its cost still shown", () => {
    const [author, check] = records.raised.visits;
    const record = {
      ...records.raised,
      visits: [must(author), { ...must(check), actions: [] }],
    };
    const runs = show(sampleProject, record);

    expect(
      runs.getByText("Exception · no action recorded · $0.00"),
    ).toBeVisible();
    expect(runs.getByText(/^done · \d+(m \d+)?s · \$/)).toBeVisible();
  });

  it("says a run whose action's times do not read has no time, not no action", () => {
    const [author, check] = records.raised.visits;
    const action = must(must(check).actions[0]);
    const record = {
      ...records.raised,
      visits: [
        must(author),
        { ...must(check), actions: [{ ...action, started_at: "not a time" }] },
      ],
    };
    const runs = show(sampleProject, record);

    expect(runs.getByText(/^Exception · — · \$/)).toBeVisible();
    expect(runs.queryByText(/no action recorded/)).toBeNull();
  });

  it("shows a run with no outcome as Not ended while its attempt runs", () => {
    const runs = show(sampleProject);

    expect(runs.getByRole("img", { name: "Not ended" })).toBeVisible();
    expect(runs.getByText(/^Not ended ·/)).toBeVisible();
  });
});

describe("a resumed attempt in the Runs column", () => {
  it("marks where it went on after its kept runs, the discarded folded", () => {
    const runs = show(sampleProject, records.resumed);
    const nav = screen.getByRole("navigation", { name: "Runs" });
    const order = [...nav.querySelectorAll("a, button, p")]
      .filter((e) => e.closest("[hidden]") === null)
      .map((e) => e.textContent);
    const group = runs.getByRole("button", { name: /Discarded/ });
    const resumed = order.indexOf("Resumed 2026-03-02 09:52 UTC");

    expect(group).toHaveAttribute("aria-expanded", "false");
    expect(group).toHaveTextContent("Discarded1 of 1 bad or warn");
    expect(order.indexOf(must(group.textContent))).toBe(resumed - 1);
    expect(order[resumed - 2]).toMatch(/^authordone · .*3$/);
    expect(order[resumed + 1]).toBe("the machine restarted while check 2 ran");
    expect(order[resumed + 2]).toBe("$0.37 carried");
    expect(order[resumed + 3]).toMatch(/^checkpass · .*4$/);
  });

  it("opens the group to the discarded run, linked to its page", () => {
    const runs = show(sampleProject, records.resumed);
    const group = runs.getByRole("button", { name: /Discarded/ });

    fireEvent.click(group);

    expect(group).toHaveAttribute("aria-expanded", "true");
    const list = must(
      document.getElementById(must(group.getAttribute("aria-controls"))),
    );
    expect(within(list).getByRole("link")).toHaveAttribute(
      "href",
      "/sample/attempt?id=batch-2%2Ftask-0005&tab=runs&discarded=0",
    );
    expect(within(list).getByRole("img", { name: "Exception" })).toBeVisible();
  });

  it("a discarded run with no copy is no link and says why", () => {
    const first = must(records.resumed.resumptions?.[0]);
    const lost = {
      ...records.resumed,
      resumptions: [
        {
          ...first,
          reason: null,
          carried_usd: 0,
          discarded: first.discarded.map((v) => ({
            ...v,
            path: null,
            reason: "the caller kept no copy",
          })),
        },
      ],
    };
    const runs = show(sampleProject, lost);

    fireEvent.click(runs.getByRole("button", { name: /Discarded/ }));

    expect(runs.getByText("the caller kept no copy")).toBeVisible();
    expect(runs.queryByRole("link", { name: /Exception/ })).toBeNull();
    expect(runs.getByText("Resumed 2026-03-02 09:52 UTC")).toBeVisible();
    expect(runs.queryByText(/carried/)).toBeNull();
  });

  it("shows a discarded run's page, its group open", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    const runs = show(sampleProject, records.resumed, 0);

    expect(runs.getByRole("button", { name: /Discarded/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(runs.getByRole("link", { name: /Exception/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByRole("heading", { name: "check · visit 2" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Left out when the attempt was resumed at 2026-03-02 09:52 UTC: the machine restarted while check 2 ran.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "Files kept" })).toBeVisible();
  });

  it("a discarded run with no outcome has ended while its attempt runs", () => {
    const first = must(records.resumed.resumptions?.[0]);
    const running = {
      ...records.resumed,
      status: null,
      resumptions: [
        {
          ...first,
          discarded: first.discarded.map((v) => ({ ...v, outcome: null })),
        },
      ],
    };
    const runs = show(sampleProject, running);
    const group = runs.getByRole("button", { name: /Discarded/ });

    fireEvent.click(group);

    const list = must(
      document.getElementById(must(group.getAttribute("aria-controls"))),
    );
    expect(within(list).getByRole("img", { name: "No outcome" })).toBeVisible();
    expect(within(list).queryByText(/Not ended/)).toBeNull();
  });

  it("opens the group when the page moves to one of its runs", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    const view = (discarded: number | null) => (
      <MemoryRouter>
        <RunsView
          project={sampleProject}
          attempt="batch-2/task-0005"
          record={records.resumed}
          run={null}
          discarded={discarded}
        />
      </MemoryRouter>
    );
    const { rerender } = render(view(null));
    const group = screen.getByRole("button", { name: /Discarded/ });
    expect(group).toHaveAttribute("aria-expanded", "false");

    rerender(view(0));

    expect(group).toHaveAttribute("aria-expanded", "true");
  });

  it("a record with no resumptions has no Discarded group", () => {
    const runs = show(sampleProject, records.passed);

    expect(runs.queryByRole("button", { name: /Discarded/ })).toBeNull();
    expect(runs.queryByText(/^Resumed/)).toBeNull();
  });
});

describe("a running attempt in the Runs column", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-02T09:45:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function showLive(live: LiveInfo, record: AttemptRecord = inReview) {
    render(
      <MemoryRouter>
        <RunsView
          project={sampleProject}
          attempt="batch-1/task-0001"
          record={record}
          live={live}
          run={null}
        />
      </MemoryRouter>,
    );
    return within(screen.getByRole("navigation", { name: "Runs" }));
  }

  it("ends with the run it is in, Running, its time going on", () => {
    const runs = showLive(runningIn());
    const last = must(runs.getByText("review").closest("div"));

    expect(runs.getByText("Runs · 5")).toBeVisible();
    expect(last.closest("a")).toBeNull();
    expect(within(last).getByRole("img", { name: "Running" })).toBeVisible();
    expect(last).toHaveTextContent("Running · 1m 21s#5");
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(last).toHaveTextContent("Running · 1m 24s#5");
  });

  it("shows a run its record holds with no outcome as Running", () => {
    const live = runningIn({
      state: "check",
      index: 1,
      recorded: true,
      since: "2026-03-02T09:26:12+00:00",
    });
    const runs = showLive(live, records.running);

    expect(runs.getByText("Runs · 2")).toBeVisible();
    expect(runs.getByRole("img", { name: "Running" })).toBeVisible();
    // The cost of the actions it has recorded so far, as the record has it.
    expect(runs.getByText(/^Running · 18m 48s · \$\d+\.\d\d$/)).toBeVisible();
    expect(runs.queryByText(/Not ended/)).toBeNull();
  });

  it("shows no run it is in when it may have stopped", () => {
    const runs = showLive(mayHaveStopped);

    expect(runs.getByText("Runs · 4")).toBeVisible();
    expect(runs.queryByRole("img", { name: "Running" })).toBeNull();
    expect(runs.queryByText("review")).toBeNull();
  });
});

describe("the run shown in the Runs column", () => {
  it("is brought to the list's top again once the definition above it loads", () => {
    // jsdom lays nothing out: the run shown sits 120px down the list.
    vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockImplementation(
      function (this: HTMLElement) {
        return this.getAttribute("aria-current") === "page" ? 120 : 0;
      },
    );
    const project = { ...sampleProject, definition: "definition.json" };
    const view = (definition: Loaded<Snapshot>) => (
      <MemoryRouter>
        <RunsView
          project={project}
          attempt="batch-1/task-0001"
          record={records.passed}
          run={3}
          definition={definition}
        />
      </MemoryRouter>
    );
    const { rerender } = render(view({ state: "loading" }));
    const list = screen.getByRole("region", { name: "Runs list" });
    expect(list.scrollTop).toBe(120);

    // The definition's links push the list down and leave it scrolled
    // elsewhere, as a browser does when the list gets shorter.
    list.scrollTop = 0;
    rerender(view({ state: "ok", data: { shape: "none" } }));
    expect(list.scrollTop).toBe(120);
    vi.restoreAllMocks();
  });
});
