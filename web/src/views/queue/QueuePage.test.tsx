import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sampleProject } from "../../../fixtures/sample";
import { POLL_MS } from "../../api/poll";
import type { Job, Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { QueuePage } from "./QueuePage";
import { isOpen } from "./status";

const project: Project = {
  ...sampleProject,
  actions: [{ name: "again", label: "Run again", queue: "fast" }],
};

const T = 1772442900;

function job(id: number, state: string, more: Partial<Job> = {}): Job {
  return {
    id,
    queue: "fast",
    labels: {
      project: "sample",
      action: "again",
      item: `batch-1/task-${id.toString()}`,
    },
    state,
    memory_mib: 2048,
    queued_at: T,
    started_at: state === "queued" ? null : T + 60,
    ended_at: state === "queued" || state === "running" ? null : T + 160,
    exit_code: state === "done" ? 0 : state === "failed" ? 1 : null,
    cancel_requested_at: null,
    ...more,
  };
}

const read = (jobs: Job[], ended = jobs.filter((j) => !isOpen(j)).length) => ({
  queues: [
    { name: "fast", limit: 2, running: 2, queued: 1 },
    { name: "slow", limit: 1, running: 0, queued: 0 },
  ],
  max_running: 3,
  ended_limit: 50,
  ended,
  jobs,
});

type Reply = [number, unknown];

function serve(replies: Record<string, Reply[]>, last?: Reply) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const [status, body] = replies[url]?.shift() ?? last ?? [404, {}];
      return Promise.resolve(new Response(JSON.stringify(body), { status }));
    }),
  );
  return calls;
}

function show() {
  render(
    <ProjectsContext value={{ state: "ok", data: [project] }}>
      <MemoryRouter>
        <QueuePage />
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const JOBS = [
  job(5, "running"),
  job(4, "running", { cancel_requested_at: T + 100 }),
  job(6, "queued", { labels: { note: "by hand" } }),
  job(3, "done"),
  job(2, "failed"),
  job(1, "canceled", { exit_code: -15 }),
];

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the queue page", () => {
  it("shows each queue and its jobs in their groups", async () => {
    serve({}, [200, read(JOBS)]);

    show();

    expect(await screen.findByText("2 of 2 running · 1 queued")).toBeVisible();
    expect(screen.getByText("The machine: 2 of 3 running")).toBeVisible();
    const group = (name: RegExp) => screen.getByRole("button", { name });
    expect(group(/^Running/)).toHaveTextContent("Running1 of 2 bad or warn");
    expect(group(/^Queued/)).toHaveTextContent("Queued1");
    expect(group(/^Ended/)).toHaveTextContent("Ended2 of 3 bad or warn");
    const row = (id: string) =>
      screen
        .getAllByRole("row")
        .find((r) => within(r).queryAllByRole("cell")[1]?.textContent === id);
    const cells = (id: string) =>
      within(row(id) ?? document.body)
        .getAllByRole("cell")
        .map((c) => c.textContent);
    expect(cells("3")).toEqual([
      "Done",
      "3",
      "fast",
      "batch-1/task-3",
      "Run again",
      "2026-03-02 09:15 UTC",
      "2026-03-02 09:16 UTC",
      "2026-03-02 09:17 UTC",
      "1m 40s",
      "0",
      "",
    ]);
    expect(cells("2")[0]).toBe("Failed");
    expect(cells("1")[0]).toBe("Canceled");
    expect(cells("1")[9]).toBe("-15");
    expect(cells("4")[0]).toBe("Canceling");
    expect(cells("6").slice(3, 5)).toEqual(["—", "note=by hand"]);
    expect(
      within(row("3") ?? document.body).getByRole("link", {
        name: "batch-1/task-3",
      }),
    ).toHaveAttribute("href", "/sample?q=batch-1%2Ftask-3");
  });

  it("offers Cancel only for a job that is open and not being canceled", async () => {
    serve({}, [200, read(JOBS)]);

    show();

    await screen.findByText("The machine: 2 of 3 running");
    const cancels = screen.getAllByRole("button", { name: "Cancel" });
    const ids = cancels.map(
      (b) => b.closest("tr")?.querySelectorAll("td")[1]?.textContent,
    );
    expect(ids).toEqual(["5", "6"]);
  });

  it("asks before it cancels a job, then posts its ID and reads again", async () => {
    const calls = serve({
      "/api/queue": [
        [200, read([job(6, "queued")])],
        [200, read([job(6, "canceled")])],
      ],
      "/api/queue/jobs/6/cancel": [[200, { job: job(6, "canceled") }]],
    });
    show();

    const cancel = await screen.findByRole("button", { name: "Cancel" });
    expect(cancel).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(cancel);
    expect(cancel).toHaveAttribute("aria-expanded", "true");
    const question = screen.getByText("Cancel job 6?");
    expect(question).toBeVisible();
    expect(cancel).toHaveAttribute(
      "aria-controls",
      question.parentElement?.id ?? "",
    );
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Cancel job" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel job" }));

    expect(await screen.findByText("Job 6 is canceled.")).toBeVisible();
    // The focus goes to what came of it, the button being gone.
    expect(document.activeElement).toBe(screen.getByRole("status"));
    expect(await screen.findByText("Canceled")).toBeVisible();
    const post = calls.find((c) => c.url === "/api/queue/jobs/6/cancel");
    expect(post?.init?.method).toBe("POST");
    expect(post?.init?.headers).toEqual({ "X-Viz-Write": "1" });
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("gives the focus back to Cancel when told to keep", async () => {
    serve({}, [200, read([job(6, "queued")])]);
    show();

    const cancel = await screen.findByRole("button", { name: "Cancel" });
    fireEvent.click(cancel);
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(screen.queryByText("Cancel job 6?")).toBeNull();
    expect(document.activeElement).toBe(cancel);
  });

  it.each([
    [50, null],
    [51, "1 older job that ended is not shown."],
    [120, "70 older jobs that ended are not shown."],
  ])("says how many of %i ended jobs it leaves out", async (total, note) => {
    const shown = Array.from({ length: 50 }, (_, i) => job(i + 1, "done"));
    serve({}, [200, read(shown, total)]);

    show();

    await screen.findByRole("button", { name: /^Ended/ });
    if (note === null)
      expect(screen.queryByText(/older jobs? that ended/)).toBeNull();
    else expect(screen.getByText(note)).toBeVisible();
  });

  it("warns that a running job's processes are sent SIGTERM", async () => {
    serve({}, [200, read([job(5, "running")])]);
    show();

    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(
      screen.getByText("Cancel job 5? Its processes are sent SIGTERM."),
    ).toBeVisible();
  });

  it("says when the site names no queue", async () => {
    serve({}, [404, { error: "not found", reason: "the site names no queue" }]);

    show();

    expect(await screen.findByText("This site names no queue.")).toBeVisible();
  });

  it("reads again while a job is open, and not once none is", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = serve({
      "/api/queue": [
        [200, read([job(6, "queued")])],
        [200, read([job(6, "done")])],
      ],
    });
    show();
    await screen.findByRole("button", { name: "Cancel" });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    await screen.findByText("Done");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * POLL_MS);
    });

    expect(calls.filter((c) => c.url === "/api/queue")).toHaveLength(2);
  });
});
