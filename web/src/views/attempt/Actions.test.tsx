import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sampleProject } from "../../../fixtures/sample";
import type { Project } from "../../api/types";
import { ItemActions } from "./Actions";

const project: Project = {
  ...sampleProject,
  actions: [
    { name: "again", label: "Run again", queue: "fast" },
    { name: "go-on", label: "Go on", queue: "slow" },
  ],
};

const ACTIONS = "/api/projects/sample/actions?item=batch-1%2Ftask-0001";

const job = {
  id: 7,
  queue: "fast",
  labels: { project: "sample", action: "again", item: "batch-1/task-0001" },
  state: "queued",
  memory_mib: 2048,
  queued_at: 1772442900,
  started_at: null,
  ended_at: null,
  exit_code: null,
  cancel_requested_at: null,
};

type Reply = [number, unknown];

/** Answer each URL with the next of its replies, and keep each request. */
function serve(replies: Record<string, Reply[]>) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const [status, body] = replies[url]?.shift() ?? [404, {}];
      return Promise.resolve(new Response(JSON.stringify(body), { status }));
    }),
  );
  return calls;
}

function show() {
  render(
    <MemoryRouter>
      <ItemActions project={project} attempt="batch-1/task-0001/a1" />
    </MemoryRouter>,
  );
}

const states = (goOn: string | null) => [
  { name: "again", label: "Run again", queue: "fast", reason: null },
  { name: "go-on", label: "Go on", queue: "slow", reason: goOn },
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a data item's actions", () => {
  it("greys an action that may not be submitted, with its reason", async () => {
    serve({
      [ACTIONS]: [[200, states("The newest attempt of this item has ended.")]],
    });

    show();

    const goOn = await screen.findByRole("button", { name: "Go on" });
    expect(goOn).toBeDisabled();
    expect(goOn).toHaveAccessibleDescription(
      "The newest attempt of this item has ended.",
    );
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
    expect(screen.getByText("batch-1/task-0001")).toBeVisible();
  });

  it("asks before it queues, then posts the action's name and the item alone", async () => {
    const submit =
      "/api/projects/sample/actions/again?item=batch-1%2Ftask-0001";
    const calls = serve({
      [ACTIONS]: [
        [200, states(null)],
        [200, states("Job 7 for this item is queued in fast.")],
      ],
      [submit]: [[201, { job }]],
    });
    show();

    fireEvent.click(await screen.findByRole("button", { name: "Run again" }));
    expect(
      screen.getByText("Queue Run again for batch-1/task-0001 in fast?"),
    ).toBeVisible();
    expect(calls.map((c) => c.url)).toEqual([ACTIONS]);
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    expect(
      await screen.findByText("Run again: queued as job 7 in fast."),
    ).toBeVisible();
    const post = calls.find((c) => c.url === submit);
    expect(post?.init?.method).toBe("POST");
    expect(post?.init?.headers).toEqual({ "X-Viz-Write": "1" });
    expect(post?.init?.body).toBeUndefined();
    // Read again after the write: the action is now greyed.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Go on" })).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
  });

  it("keeps nothing when told to keep", async () => {
    const calls = serve({ [ACTIONS]: [[200, states(null)]] });
    show();

    fireEvent.click(await screen.findByRole("button", { name: "Go on" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(screen.getByRole("button", { name: "Go on" })).toBeEnabled();
    expect(calls.every((c) => c.init?.method !== "POST")).toBe(true);
  });

  it("says why a write was refused", async () => {
    const submit =
      "/api/projects/sample/actions/go-on?item=batch-1%2Ftask-0001";
    serve({
      [ACTIONS]: [
        [200, states(null)],
        [200, states(null)],
      ],
      [submit]: [
        [
          409,
          {
            error: "conflict",
            reason: "An attempt of this item is running: its lock is held.",
          },
        ],
      ],
    });
    show();

    fireEvent.click(await screen.findByRole("button", { name: "Go on" }));
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    expect(
      await screen.findByText(
        "Go on was not queued (409: An attempt of this item is running: its lock is held.).",
      ),
    ).toBeVisible();
  });
});
