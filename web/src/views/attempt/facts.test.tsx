import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  inReview,
  mayHaveStopped,
  must,
  records,
  runningIn,
  sampleProject,
} from "../../../fixtures/sample";
import type { AttemptRecord, LiveInfo } from "../../api/types";
import { AttemptFacts } from "./facts";

function show(
  record: AttemptRecord,
  live: LiveInfo | null = null,
  project = sampleProject,
) {
  const shown = render(
    <AttemptFacts project={project} record={record} live={live} />,
  );
  const item = (key: string) => must(screen.getByText(key).nextElementSibling);
  return { ...shown, item };
}

describe("an attempt's cost", () => {
  it("says what of it the resumptions carried over", () => {
    const { item } = show(records.resumed);

    expect(item("Cost")).toHaveTextContent(
      "$5.71 of $40.00$0.37 carried over 1 resumption",
    );
  });

  it("says nothing of carried cost when nothing was resumed", () => {
    show(records.passed);

    expect(screen.queryByText(/carried over/)).toBeNull();
  });

  it("is its runs' with no cap, for a record with no budget", () => {
    const { item } = show({ ...records.passed, budget: undefined });

    // The sample's budget holds what its runs cost, $5.34.
    expect(item("Cost")).toHaveTextContent("$5.34 of —from its runs");
  });
});

describe("an attempt's status", () => {
  it("is Exception when a state raised, else Not ended until it ends", () => {
    const { unmount, item } = show(records.raised);
    expect(item("Status")).toHaveTextContent(/^Exception$/);
    unmount();

    expect(show(records.running).item("Status")).toHaveTextContent(
      /^Not ended$/,
    );
  });

  it("is Not ended while its status is one the project names as not ended", () => {
    const { item } = show(records.passed, null, {
      ...sampleProject,
      not_ended: ["passed"],
    });

    expect(item("Status")).toHaveTextContent(/^Not ended$/);
  });

  it("gives the record's reason on hover and on focus", async () => {
    const { item } = show({ ...records.passed, reason: "4 of 4 solved" });
    const label = item("Status").querySelector("[tabindex]");
    if (!(label instanceof HTMLElement)) throw new Error("no focusable label");
    fireEvent.focus(label);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "4 of 4 solved",
    );
  });

  it("is Stopped, with the job that ran it on focus, once that job has ended", async () => {
    const { item } = show(inReview, {
      ...mayHaveStopped,
      stopped: { job: 7, state: "canceled" },
    });

    expect(item("Status")).toHaveTextContent(/^Stopped$/);
    const label = item("Status").querySelector("[tabindex]");
    if (!(label instanceof HTMLElement)) throw new Error("no focusable label");
    fireEvent.focus(label);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Job 7, which ran this attempt, ended Canceled; nothing runs it now.",
    );
  });
});

describe("an attempt's total duration", () => {
  it("runs from its first action's start to its last action's end, a last run with no action as well", () => {
    const last = must(records.passed.visits.at(-1));
    const ended = {
      ...records.passed,
      visits: [...records.passed.visits, { ...last, actions: [] }],
    };

    expect(show(ended).item("Total duration")).toHaveTextContent("1h 10m");
  });

  it("ends where a last run with no action ended, by its own times", () => {
    const last = must(records.passed.visits.at(-1));
    const end = Date.parse(must(last.actions.at(-1)).ended_at);
    const at = (minutes: number) =>
      new Date(end + minutes * 60_000).toISOString();
    const raised = {
      ...last,
      outcome: "raised",
      actions: [],
      started_at: at(0),
      ended_at: at(10),
    };
    const ended = {
      ...records.passed,
      visits: [...records.passed.visits, raised],
    };

    expect(show(ended).item("Total duration")).toHaveTextContent("1h 20m");
  });

  it("ends where the last run that ended did, after one entered and never ended", () => {
    const last = must(records.passed.visits.at(-1));
    const end = must(last.actions.at(-1)).ended_at;
    const entered = {
      ...last,
      outcome: null,
      actions: [],
      started_at: end,
      ended_at: null,
    };
    const ended = {
      ...records.passed,
      visits: [...records.passed.visits, entered],
    };

    expect(show(ended).item("Total duration")).toHaveTextContent("1h 10m");
  });
});

describe("a running attempt's facts", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-02T09:45:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("say Running and its total goes on from its start", () => {
    const { item } = show(inReview, runningIn());

    expect(item("Status")).toHaveTextContent("Running");
    expect(item("Total duration")).toHaveTextContent("30m 0s");
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(item("Total duration")).toHaveTextContent("30m 2s");
    // The cost is the record's, and does not go on.
    expect(item("Cost")).toHaveTextContent("$");
  });

  it("count the total on from the job's start before any action has one", () => {
    const first = {
      ...inReview,
      visits: [{ ...must(inReview.visits[0]), outcome: null, actions: [] }],
    };
    const { item } = show(
      first,
      runningIn({
        index: 0,
        recorded: true,
        since: "2026-03-02T09:30:00+00:00",
      }),
    );

    expect(item("Started")).toHaveTextContent("2026-03-02 09:30 UTC");
    expect(item("Total duration")).toHaveTextContent("15m 0s");
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(item("Total duration")).toHaveTextContent("15m 3s");
  });

  it("take a record that has ended over a /live read before it ended", () => {
    const { item } = show(
      records.passed,
      runningIn({ index: records.passed.visits.length }),
    );

    expect(item("Status")).toHaveTextContent("Passed");
    expect(item("Total duration")).toHaveTextContent("1h 10m");
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(item("Total duration")).toHaveTextContent("1h 10m");
  });

  it("stop the total at the record's when it may have stopped", () => {
    const { item } = show(inReview, mayHaveStopped);

    expect(item("Status")).toHaveTextContent("May have stopped");
    expect(item("Total duration")).toHaveTextContent("28m 39s");
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(item("Total duration")).toHaveTextContent("28m 39s");
  });
});
